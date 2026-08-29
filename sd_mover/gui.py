"""CustomTkinter GUI for the EZMovr."""

import os
import queue
import threading
import time
import customtkinter as ctk
from pathlib import Path
from tkinter import filedialog, messagebox

from PIL import Image, ImageOps

from .drive_detector import get_removable_drives, format_drive_info
from .file_scanner import (
    scan_drive,
    filter_new_files,
    get_files_summary,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    RAW_EXTENSIONS,
)
from .folder_builder import get_destination, ensure_folder
from .file_copier import copy_files
from . import settings
from .onboarding import OnboardingWindow

ACCENT = "#3B82F6"
ACCENT_HOVER = "#2563EB"
SUCCESS = "#22C55E"
SUCCESS_HOVER = "#16A34A"
WARN = "#F59E0B"
DANGER = "#EF4444"
VIDEO_COLOR = "#8B5CF6"
RAW_COLOR = "#D97706"
EMPTY_MSG = "Insert an SD card and click Scan to begin."
THUMB_SIZE = (120, 90)
THUMB_PAD = 6
COLS = 3

CARD_FG = ("gray92", "#1f1f1f")
CARD_BORDER = ("gray85", "#2d2d2d")

# "Super black" OLED surface colors (pure black backgrounds).
OLED_BG = "#000000"
OLED_CARD_FG = "#000000"
OLED_CARD_BORDER = "#161616"
OLED_DIVIDER = "#121212"
OLED_TEXTBOX = "#0a0a0a"
OLED_DROPDOWN = "#0d0d0d"
OLED_DROPDOWN_HOVER = "#1c1c1c"
OLED_SCROLLBAR = "#222222"
OLED_UNDERLINE = "#222222"

_BADGE_GLYPHS = {"video": "||", "raw": "RAW", "fail": "!", "other": "FILE",
                 "img": "IMG"}


def badge_text(kind):
    return _BADGE_GLYPHS.get(kind, "FILE")


class PhotoPreviewPanel(ctk.CTkScrollableFrame):
    """Scrollable grid of photo thumbnails, rendered asynchronously.

    Files are grouped into sections: RAW, Portrait, Landscape, and Video.
    Thumbnail decoding happens on a worker thread and the UI is updated a few
    widgets at a time so the app never freezes, even with thousands of files.
    """

    CHUNK = 24          # widgets rendered per UI tick
    BATCH = 12          # items queued per worker loop
    POLL_MS = 40        # UI pump interval

    SECTION_ORDER = ["raw", "portrait", "landscape", "video"]
    SECTION_LABELS = {
        "raw": "RAW",
        "portrait": "Portrait",
        "landscape": "Landscape",
        "video": "Video",
    }

    def __init__(self, master, **kwargs):
        super().__init__(master, label_text="Preview", **kwargs)
        self._thumbs = []  # keep CTkImage refs alive (prevent GC)
        self._queue = queue.Queue()
        self._gen_id = 0
        self._rendered = 0
        self._total = 0
        self._loading_label = None
        self._current_section = None
        self._section_counts = {}
        self._row_frames = []
        self._row_count = 0
        self._sections = []  # [{cat, cards:[...], header:widget}]
        self._relayout_pending = False
        self._last_relayout_width = 0
        self._oled = False
        self.bind("<Configure>", self._on_resize)

    # ---- theme ----

    def set_oled(self, on):
        """Switch the preview's surfaces to pure-black OLED colors."""
        self._oled = bool(on)
        self._recolor_surfaces()

    def _oled_val(self, normal, oled):
        return oled if self._oled else normal

    def _recolor_surfaces(self):
        def walk(w):
            if getattr(w, "_is_card", False):
                w.configure(
                    fg_color=self._oled_val(CARD_FG, OLED_CARD_FG),
                    border_color=self._oled_val(CARD_BORDER, OLED_CARD_BORDER),
                )
            if getattr(w, "_is_underline", False):
                w.configure(fg_color=self._oled_val(("gray85", "#333"), OLED_UNDERLINE))
            for child in w.winfo_children():
                walk(child)
        walk(self)

    # ---- public API ----

    def clear(self):
        self._gen_id += 1
        with self._queue.mutex:
            self._queue.queue.clear()
        for w in self.winfo_children():
            w.destroy()
        self._thumbs.clear()
        self._row_frames.clear()
        self._current_section = None
        self._row_count = 0
        self._sections = []
        self._relayout_pending = False
        self._rendered = 0
        self._loading_label = None

    def load_files(self, files):
        self.clear()
        self._total = len(files)
        if not files:
            ctk.CTkLabel(
                self, text="No previews available", font=("", 13),
                text_color="gray50",
            ).pack(pady=30)
            return

        self._loading_label = ctk.CTkLabel(
            self,
            text=f"Loading previews... 0/{len(files)}",
            font=("", 11), text_color="gray55",
        )
        self._loading_label.pack(pady=6)

        gen = self._gen_id
        threading.Thread(target=self._worker, args=(gen, files), daemon=True).start()
        self.after(self.POLL_MS, self._pump, gen)

    # ---- background work ----

    def _worker(self, gen, files):
        if self._gen_id != gen:
            return

        # Phase 1 — cheap categorization (header reads only, no pixel decode).
        categorized = []
        for f in files:
            if self._gen_id != gen:
                return
            categorized.append((self._categorize(f), f))

        counts = {}
        for cat, _ in categorized:
            counts[cat] = counts.get(cat, 0) + 1
        self._queue.put([("__counts__", counts)])

        # Sort so each section stays contiguous in the preview.
        order = {c: i for i, c in enumerate(self.SECTION_ORDER)}
        categorized.sort(key=lambda x: order.get(x[0], 99))

        # Phase 2 — decode thumbnails (single open each) and stream them.
        batch = []
        for cat, f in categorized:
            if self._gen_id != gen:
                return
            kind, pil = self._classify(f)
            batch.append((cat, f.name, kind, pil))
            if len(batch) >= self.BATCH:
                self._queue.put(batch)
                batch = []
                time.sleep(0.004)  # let the UI pump keep pace
        if batch:
            self._queue.put(batch)

    def _categorize(self, path):
        """Return the section a file belongs to (cheap header read, no decode)."""
        ext = path.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            return "video"
        if ext in RAW_EXTENSIONS:
            return "raw"
        if ext in IMAGE_EXTENSIONS:
            w, h = self._effective_size(path)
            return "portrait" if h > w else "landscape"
        return "landscape"

    def _effective_size(self, path):
        """Image dimensions after EXIF rotation (so portrait stays portrait)."""
        try:
            with Image.open(path) as img:
                w, h = img.size
                orient = img.getexif().get(0x0112, 1)
                if orient in (5, 6, 7, 8):  # image is rotated 90/270
                    w, h = h, w
                return w, h
        except Exception:
            return (1, 1)

    def _classify(self, path):
        ext = path.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            return "video", None
        if ext in RAW_EXTENSIONS:
            pil = self._load_raw_pil(path)
            return ("img", pil) if pil is not None else ("raw", None)
        if ext in IMAGE_EXTENSIONS:
            pil = self._load_pil(path)
            return ("img", pil) if pil is not None else ("fail", None)
        return "other", None

    def _load_pil(self, path):
        img = None
        try:
            img = Image.open(path)
            img.draft("RGB", THUMB_SIZE)  # fast JPEG decode header preview
            img = ImageOps.exif_transpose(img)  # honor camera orientation
            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
            out = img.convert("RGB").copy()
            return out
        except Exception:
            return None
        finally:
            if img is not None:
                img.close()

    def _load_raw_pil(self, path):
        """Render a RAW preview via rawpy (CR2, NEF, ARW, DNG, etc.)."""
        try:
            import rawpy
            with rawpy.imread(str(path)) as raw:
                rgb = raw.postprocess(use_camera_wb=True, half_size=True)
                orient = int(getattr(raw, "orientation", 1) or 1)
            img = Image.fromarray(rgb)
            if orient == 3:
                img = img.rotate(180)
            elif orient in (5, 6, 7, 8):
                img = img.rotate(270)
            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
            return img.convert("RGB")
        except Exception:
            return None

    # ---- UI-side rendering ----

    def _pump(self, gen):
        if gen != self._gen_id:
            return
        rendered = 0
        while rendered < self.CHUNK:
            try:
                items = self._queue.get_nowait()
            except queue.Empty:
                break
            for item in items:
                if item[0] == "__counts__":
                    self._section_counts = item[1]
                    continue
                category, name, kind, pil = item
                self._add_thumb(category, name, kind, pil)
                rendered += 1
        self._rendered += rendered

        if self._loading_label is not None:
            self._loading_label.configure(
                text=f"Loading previews... {self._rendered}/{self._total}"
            )
            if self._rendered >= self._total:
                self._loading_label.destroy()
                self._loading_label = None
            else:
                self.after(self.POLL_MS, self._pump, gen)

    # ---- responsive layout ----

    def _compute_cols(self):
        """How many thumbnail columns fit in the current panel width."""
        try:
            width = self.winfo_width()
        except Exception:
            width = 360
        # each column needs the thumb width + padding + breathing room
        cell = THUMB_SIZE[0] + 2 * THUMB_PAD + 18
        return max(1, min(width // cell, 8))

    def _on_resize(self, event):
        if event.widget is not self or self._relayout_pending:
            return
        # Only relayout on actual width changes — repacking thumbnails fires
        # Configure events too, and relayouting on those would loop forever.
        if self.winfo_width() == self._last_relayout_width:
            return
        self._relayout_pending = True
        self.after(120, self._do_relayout)

    def _do_relayout(self):
        self._relayout_pending = False
        self._relayout()

    def _relayout(self):
        if not self._sections:
            return
        # Unbind during the rebuild so our own repacking can't retrigger a
        # relayout (which would freeze the UI in an infinite loop).
        self.unbind("<Configure>")
        try:
            self._last_relayout_width = self.winfo_width()
            cols = self._compute_cols()
            self._relayout_impl(cols)
        finally:
            self.bind("<Configure>", self._on_resize)

    def _relayout_impl(self, cols):
        # Reparent every card up to `self` so destroying row frames won't
        # delete them, then drop the old headers and row frames.
        for sec in self._sections:
            for c in sec["cards"]:
                c.pack(in_=self)
            if sec.get("header") is not None:
                sec["header"].destroy()
                sec["header"] = None
        for r in self._row_frames:
            r.destroy()
        self._row_frames = []
        self._current_section = None

        for sec in self._sections:
            self._current_section = sec["cat"]
            sec["header"] = self._make_header(sec["cat"])
            self._row_frames = []
            self._row_count = 0
            for i, card in enumerate(sec["cards"]):
                if i % cols == 0:
                    row = ctk.CTkFrame(self, fg_color="transparent")
                    row.pack(fill="x", pady=THUMB_PAD)
                    self._row_frames.append(row)
                cell = ctk.CTkFrame(self._row_frames[-1], fg_color="transparent")
                cell.pack(side="left", padx=THUMB_PAD, expand=True, fill="x")
                card.pack(in_=cell, padx=4, pady=(4, 0))
                self._row_count += 1

    def _make_header(self, category):
        label = self.SECTION_LABELS.get(category, category.title())
        count = self._section_counts.get(category)
        text = f"{label}    {count}" if count else label

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=2, pady=(12, 4))

        ctk.CTkLabel(
            head,
            text=text,
            font=("", 13, "bold"),
            text_color=ACCENT,
            anchor="w",
        ).pack(side="left")

        ul = ctk.CTkFrame(
            head, height=2, fg_color=self._oled_val(("gray85", "#333"), OLED_UNDERLINE),
            corner_radius=1,
        )
        ul._is_underline = True
        ul.pack(side="left", fill="x", expand=True, padx=8)
        return head

    def _start_section(self, category):
        self._current_section = category
        self._sections.append({"cat": category, "cards": [], "header": None})
        self._row_frames.clear()
        self._row_count = 0
        self._sections[-1]["header"] = self._make_header(category)

    def _add_thumb(self, category, name, kind, pil):
        if category != self._current_section:
            self._start_section(category)

        cols = self._compute_cols()
        if self._row_count % cols == 0:
            row = ctk.CTkFrame(self, fg_color="transparent")
            row.pack(fill="x", pady=THUMB_PAD)
            self._row_frames.append(row)
        parent = self._row_frames[-1]

        cell = ctk.CTkFrame(parent, fg_color="transparent")
        cell.pack(side="left", padx=THUMB_PAD, expand=True, fill="x")
        self._row_count += 1

        card = self._build_card(name, kind, pil)
        card.pack(in_=cell, padx=4, pady=(4, 0))
        self._sections[-1]["cards"].append(card)

    def _build_card(self, name, kind, pil):
        card = ctk.CTkFrame(
            self,
            fg_color=self._oled_val(CARD_FG, OLED_CARD_FG),
            corner_radius=8,
            border_width=1,
            border_color=self._oled_val(CARD_BORDER, OLED_CARD_BORDER),
        )
        card._is_card = True
        card.bind("<Enter>", lambda e, c=card: c.configure(border_color=ACCENT))
        card.bind(
            "<Leave>",
            lambda e, c=card: c.configure(
                border_color=self._oled_val(CARD_BORDER, OLED_CARD_BORDER)
            ),
        )

        if kind == "img":
            ctk_img = ctk.CTkImage(
                light_image=pil, dark_image=pil, size=(pil.width, pil.height)
            )
            lbl = ctk.CTkLabel(card, image=ctk_img, text="")
            lbl.image = ctk_img  # keep ref alive
            lbl.pack(padx=4, pady=(4, 0))
            self._thumbs.append(ctk_img)
        else:
            badge, subtitle = {
                "video": (VIDEO_COLOR, "Video"),
                "raw": (RAW_COLOR, "RAW"),
                "fail": (DANGER, "Cannot preview"),
                "other": ("gray55", "File"),
            }.get(kind, ("gray55", "File"))
            box = ctk.CTkFrame(card, fg_color="transparent")
            box.pack(expand=True, fill="both", padx=4, pady=(8, 0))
            ctk.CTkLabel(
                box, text=badge_text(kind), font=("", 20, "bold"),
                text_color=badge, height=THUMB_SIZE[1] // 2,
            ).pack(expand=True)
            ctk.CTkLabel(
                box, text=subtitle, font=("", 10),
                text_color="gray60",
            ).pack()

        short = name if len(name) < 18 else name[:15] + "..."
        ctk.CTkLabel(card, text=short, font=("Consolas", 9), text_color="gray70").pack(
            padx=4, pady=(0, 4),
        )
        return card


class SDMoverApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("EZMovr")
        self.geometry("1050x700")
        self.minsize(900, 600)

        ctk.set_default_color_theme("blue")

        self.drives = []
        self.selected_drive = None
        self.scanned_files = []
        self.copying = False
        self._oled = False
        self._surfaces = []  # (widget, attr, normal, oled)

        # Match the root background to the theme colors so maximized/resized
        # areas repaint instantly instead of flashing black.
        self.configure(fg_color=("#ebebeb", "#242424"))
        self._register_surface(self, "fg_color", ("#ebebeb", "#242424"), OLED_BG)
        self.bind("<Configure>", self._on_window_resize)

        self._build_ui()
        self._load_saved_settings()
        self._center_window()
        self._scan_drives()

        self.after(100, self._check_onboarding)

    def _center_window(self):
        """Center the window on screen."""
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max((sw - w) // 2, 0)
        y = max((sh - h) // 2, 0)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _on_window_resize(self, event):
        """Repaint immediately after resize/maximize to avoid black gaps."""
        if event.widget is self:
            self.after_idle(self._repaint)

    def _repaint(self):
        self.update_idletasks()

    def _build_ui(self):
        # --- Header ---
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(14, 8))

        ctk.CTkLabel(
            header, text="EZ", font=("", 17, "bold"), text_color="white",
            fg_color=ACCENT, corner_radius=8, width=42, height=42,
        ).pack(side="left")

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", padx=10)
        ctk.CTkLabel(
            title_box, text="EZMovr", font=("", 21, "bold"), anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_box,
            text="Transfer photos and videos from your camera to your PC.",
            font=("", 11), text_color="gray55", anchor="w",
        ).pack(anchor="w")

        # --- Theme toggle ---
        theme_frame = ctk.CTkFrame(header, fg_color="transparent")
        theme_frame.pack(side="right")

        ctk.CTkLabel(
            theme_frame, text="Theme:", font=("", 11), text_color="gray55",
        ).pack(side="left", padx=(0, 4))

        self.theme_var = ctk.StringVar(value="system")
        self.theme_menu = ctk.CTkSegmentedButton(
            theme_frame,
            variable=self.theme_var,
            values=["system", "light", "dark", "oled"],
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            command=self._on_theme_change,
            font=("", 11),
            width=240,
            height=30,
        )
        self.theme_menu.pack(side="left")

        # --- Divider ---
        self.divider = ctk.CTkFrame(
            self, height=1, fg_color=("gray85", "#2a2a2a"),
        )
        self.divider.pack(fill="x", padx=20, pady=(0, 6))
        self._register_surface(self.divider, "fg_color", ("gray85", "#2a2a2a"), OLED_DIVIDER)

        # --- Two-column body ---
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=(0, 4))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(
            body, fg_color="transparent", corner_radius=0,
            scrollbar_button_color=("gray75", "#3a3a3a"),
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._register_surface(
            left, "scrollbar_button_color", ("gray75", "#3a3a3a"), OLED_SCROLLBAR
        )

        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._build_left_panel(left)
        self._build_right_panel(right)

        # --- Footer status bar ---
        self.footer = ctk.CTkLabel(
            self, text="Ready", font=("", 10), text_color="gray55", anchor="w",
        )
        self.footer.pack(fill="x", padx=20, pady=(2, 8))

    # ---- Left panel ----

    def _card(self, parent, title, subtitle=None):
        """Return a rounded card frame with a header row."""
        card = ctk.CTkFrame(
            parent, fg_color=CARD_FG, corner_radius=10,
            border_width=1, border_color=CARD_BORDER,
        )
        card.pack(fill="x", pady=(0, 8))
        self._register_surface(card, "fg_color", CARD_FG, OLED_CARD_FG)
        self._register_surface(card, "border_color", CARD_BORDER, OLED_CARD_BORDER)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(10, 2))
        ctk.CTkLabel(head, text=title, font=("", 13, "bold")).pack(side="left")
        if subtitle:
            ctk.CTkLabel(
                head, text=subtitle, font=("", 10), text_color="gray55",
            ).pack(side="right")
        return card

    def _make_chip(self, parent, text, color):
        ctk.CTkLabel(
            parent, text=text, font=("", 10, "bold"), text_color="white",
            fg_color=color, corner_radius=8, padx=8, pady=2,
        ).pack(side="left", padx=(0, 6))

    def _build_left_panel(self, parent):
        # Drive
        self._drive_section(parent)
        # Settings
        self._settings_section(parent)
        # Scan button
        self.scan_btn = ctk.CTkButton(
            parent, text="Scan SD Card", height=38,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._start_scan, font=("", 12, "bold"),
        )
        self.scan_btn.pack(fill="x", pady=(0, 4))

        self.scan_status = ctk.CTkLabel(
            parent, text="", font=("", 11), text_color="gray55", anchor="w",
        )
        self.scan_status.pack(fill="x", padx=2, pady=(0, 8))

        # File list
        self._files_card(parent)

        # Progress + Copy
        self._progress_card(parent)

    def _files_card(self, parent):
        card = self._card(parent, "Files", subtitle="Media found on the card")

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(0, 4))

        self.chip_frame = ctk.CTkFrame(card, fg_color="transparent")
        self.chip_frame.pack(fill="x", padx=14, pady=(0, 6))

        self.file_count_label = ctk.CTkLabel(
            top, text="", font=("", 11), text_color="gray55",
        )
        self.file_count_label.pack(side="left")

        self.chip_hint = ctk.CTkLabel(
            self.chip_frame, text="No scan yet", font=("", 10),
            text_color="gray55",
        )
        self.chip_hint.pack(side="left")

        self.file_textbox = ctk.CTkTextbox(
            card, state="disabled", font=("Consolas", 11),
            fg_color=("gray97", "#161616"),
            border_width=1, border_color=CARD_BORDER, corner_radius=8,
            height=170,
        )
        self.file_textbox.pack(fill="x", padx=14, pady=(0, 12))
        self._register_surface(self.file_textbox, "fg_color", ("gray97", "#161616"), OLED_TEXTBOX)
        self._register_surface(self.file_textbox, "border_color", CARD_BORDER, OLED_CARD_BORDER)
        self._set_placeholder()

    def _progress_card(self, parent):
        card = self._card(parent, "Transfer", subtitle="Copy selected files")

        self.progress_bar = ctk.CTkProgressBar(
            card, height=10, fg_color=("gray85", "#2e2e2e"),
            progress_color=ACCENT, corner_radius=5,
        )
        self.progress_bar.pack(fill="x", padx=14, pady=(2, 0))
        self._register_surface(self.progress_bar, "fg_color", ("gray85", "#2e2e2e"), OLED_CARD_BORDER)
        self.progress_bar.set(0)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(6, 0))

        self.progress_pct = ctk.CTkLabel(
            row, text="0%", font=("", 11, "bold"), text_color=ACCENT,
        )
        self.progress_pct.pack(side="right")

        self.progress_label = ctk.CTkLabel(
            row, text="Waiting to copy", font=("", 10), text_color="gray55",
        )
        self.progress_label.pack(side="left")

        self.copy_btn = ctk.CTkButton(
            card, text="Copy Files", height=36,
            fg_color=SUCCESS, hover_color=SUCCESS_HOVER,
            command=self._start_copy, font=("", 12, "bold"),
        )
        self.copy_btn.pack(fill="x", padx=14, pady=8)

    # ---- Right panel (preview) ----

    def _build_right_panel(self, parent):
        self.preview = PhotoPreviewPanel(
            parent,
            fg_color=("gray90", "#1e1e1e"),
            corner_radius=8,
        )
        self.preview.pack(fill="both", expand=True)
        self._register_surface(self.preview, "fg_color", ("gray90", "#1e1e1e"), OLED_BG)

    # ---- Drive section ----

    def _drive_section(self, parent):
        frame = self._card(parent, "SD Card")

        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(4, 8))

        # Status dot + text
        status_box = ctk.CTkFrame(row, fg_color="transparent")
        status_box.pack(side="right")
        self.drive_dot = ctk.CTkLabel(
            status_box, text="", width=10, height=10,
            fg_color=("gray65", "#555"), corner_radius=5,
        )
        self.drive_dot.pack(side="left", padx=(0, 5))
        self.drive_status = ctk.CTkLabel(
            status_box, text="Checking...", font=("", 10),
        )
        self.drive_status.pack(side="left")

        self.drive_var = ctk.StringVar(value="No drives found")
        self.drive_menu = ctk.CTkOptionMenu(
            row, variable=self.drive_var,
            values=["No drives found"],
            fg_color=("gray88", "#2b2b2b"), button_color=ACCENT,
            button_hover_color=ACCENT_HOVER, dropdown_fg_color=("gray95", "#2b2b2b"),
            dropdown_hover_color=("gray85", "#3a3a3a"), height=34,
            anchor="w",
        )
        self.drive_menu.pack(side="left", fill="x", expand=True)
        self._register_surface(self.drive_menu, "fg_color", ("gray88", "#2b2b2b"), OLED_DROPDOWN)
        self._register_surface(self.drive_menu, "dropdown_fg_color", ("gray95", "#2b2b2b"), OLED_DROPDOWN)
        self._register_surface(self.drive_menu, "dropdown_hover_color", ("gray85", "#3a3a3a"), OLED_DROPDOWN_HOVER)

        ctk.CTkButton(
            row, text="Refresh", width=72, height=34,
            fg_color=("gray65", "#3a3a3a"), hover_color=("gray55", "#4a4a4a"),
            command=self._scan_drives,
        ).pack(side="left", padx=(8, 0))

    # ---- Settings section ----

    def _settings_section(self, parent):
        frame = self._card(parent, "Options")

        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=(4, 10))

        ctk.CTkLabel(inner, text="Copy mode", font=("", 11, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 3),
        )

        mode_row = ctk.CTkFrame(inner, fg_color="transparent")
        mode_row.grid(row=1, column=0, sticky="w", pady=(0, 8))

        self.mode_var = ctk.StringVar(value="all")
        ctk.CTkRadioButton(
            mode_row, text="All files",
            variable=self.mode_var, value="all",
        ).pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(
            mode_row, text="Only new files (skip duplicates)",
            variable=self.mode_var, value="new",
        ).pack(side="left")

        ctk.CTkLabel(inner, text="Destination", font=("", 11, "bold")).grid(
            row=2, column=0, sticky="w", pady=(0, 3),
        )

        dest_row = ctk.CTkFrame(inner, fg_color="transparent")
        dest_row.grid(row=3, column=0, sticky="w", pady=(0, 6))

        self.dest_mode_var = ctk.StringVar(value="date")
        ctk.CTkRadioButton(
            dest_row, text="Date-based",
            variable=self.dest_mode_var, value="date",
            command=self._toggle_dest_name,
        ).pack(side="left", padx=(0, 12))
        ctk.CTkRadioButton(
            dest_row, text="Custom name",
            variable=self.dest_mode_var, value="named",
            command=self._toggle_dest_name,
        ).pack(side="left", padx=(0, 8))

        self.folder_name_entry = ctk.CTkEntry(
            dest_row, placeholder_text="Folder name", width=150, state="disabled",
        )
        self.folder_name_entry.pack(side="left")

        base_row = ctk.CTkFrame(inner, fg_color="transparent")
        base_row.grid(row=4, column=0, sticky="w", pady=(4, 0))

        ctk.CTkLabel(base_row, text="Save to:", font=("", 11)).pack(side="left")
        self.base_folder_var = ctk.StringVar(value=str(Path.home() / "Pictures"))
        ctk.CTkEntry(
            base_row, textvariable=self.base_folder_var, width=150,
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            base_row, text="Browse", width=65, height=28,
            fg_color=("gray65", "#3a3a3a"), hover_color=("gray55", "#4a4a4a"),
            command=self._browse_folder,
        ).pack(side="left")

    # ---- Helpers ----

    def _set_placeholder(self):
        self.file_textbox.configure(state="normal")
        self.file_textbox.delete("1.0", "end")
        self.file_textbox.insert("1.0", EMPTY_MSG)
        self.file_textbox.configure(state="disabled")

    def _toggle_dest_name(self):
        if self.dest_mode_var.get() == "named":
            self.folder_name_entry.configure(state="normal")
        else:
            self.folder_name_entry.delete(0, "end")
            self.folder_name_entry.configure(state="disabled")

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Select base destination folder")
        if folder:
            self.base_folder_var.set(folder)

    # ---- Theme ----

    def _register_surface(self, widget, attr, normal, oled):
        """Record a widget color so it can switch to OLED blacks."""
        self._surfaces.append((widget, attr, normal, oled))
        try:
            widget.configure(**{attr: oled if self._oled else normal})
        except Exception:
            pass

    def _apply_surface_theme(self):
        for widget, attr, normal, oled in self._surfaces:
            try:
                widget.configure(**{attr: oled if self._oled else normal})
            except Exception:
                pass
        if getattr(self, "preview", None) is not None:
            self.preview.set_oled(self._oled)

    def _set_theme(self, choice):
        choice = (choice or "system").lower()
        if choice == "oled":
            self._oled = True
            mode = "dark"
        elif choice in ("dark", "light", "system"):
            self._oled = False
            mode = choice
        else:
            self._oled = False
            mode = "system"
        ctk.set_appearance_mode(mode)
        self._apply_surface_theme()

    def _on_theme_change(self, choice):
        self._set_theme(choice)
        settings.set("theme", (choice or "system").lower())

    def _scan_drives(self):
        self.drives = get_removable_drives()
        if not self.drives:
            self.drive_menu.configure(values=["No removable drives found"])
            self.drive_var.set("No removable drives found")
            self.drive_status.configure(text="Insert an SD card", text_color=WARN)
            self.drive_dot.configure(fg_color=WARN)
            return

        labels = [format_drive_info(d) for d in self.drives]
        self.drive_menu.configure(values=labels)
        self.drive_var.set(labels[0])
        self.selected_drive = self.drives[0]
        self.drive_status.configure(text=f"{len(self.drives)} found", text_color=SUCCESS)
        self.drive_dot.configure(fg_color=SUCCESS)

    def _get_selected_drive_path(self):
        for d in self.drives:
            if self.drive_menu.get().startswith(d["letter"]):
                return d["mountpoint"]
        return None

    # ---- Scan ----

    def _start_scan(self):
        drive_path = self._get_selected_drive_path()
        if not drive_path:
            messagebox.showwarning("No Drive", "No removable drive selected.")
            return

        self.scan_btn.configure(state="disabled", text="Scanning...")
        self.scan_status.configure(text="Scanning for media files...")
        self.file_count_label.configure(text="")
        self.chip_hint.configure(text="Scanning...")
        self._set_placeholder()
        self.preview.clear()

        threading.Thread(target=self._scan_thread, args=(drive_path,), daemon=True).start()

    def _scan_progress(self, count):
        self.scan_status.configure(text=f"Scanning... {count} files found")

    def _scan_thread(self, drive_path):
        def on_progress(count):
            self.after(0, self._scan_progress, count)

        files = scan_drive(drive_path, progress_cb=on_progress)

        if self.mode_var.get() == "new":
            dest = get_destination(
                self.dest_mode_var.get(),
                self.base_folder_var.get(),
                self.folder_name_entry.get(),
            )
            files, dupe_count = filter_new_files(files, dest)
            msg = f"Found {len(files)} new files"
            if dupe_count > 0:
                msg += f" ({dupe_count} duplicates skipped)"
        else:
            msg = f"Found {len(files)} files"

        self.scanned_files = files
        self.after(0, self._scan_done, files, msg)

    def _scan_done(self, files, msg):
        self.scan_btn.configure(state="normal", text="Scan SD Card")
        self.scan_status.configure(text=msg)
        self.footer.configure(text=msg)

        if files:
            summary = get_files_summary(files)
            self._set_file_list_text(summary)
            self.file_count_label.configure(text=f"{len(files)} files")
            self._set_chips(files)
            self.copy_btn.configure(state="normal")
            self.preview.load_files(files)
        else:
            self._set_placeholder()
            self.file_count_label.configure(text="")
            self.chip_hint.configure(text="No media files found")
            self.copy_btn.configure(state="disabled")
            self.preview.clear()

    def _set_chips(self, files):
        for w in self.chip_frame.winfo_children():
            w.destroy()

        counts = {"photos": 0, "videos": 0, "raw": 0}
        for f in files:
            ext = f.suffix.lower()
            if ext in VIDEO_EXTENSIONS:
                counts["videos"] += 1
            elif ext in RAW_EXTENSIONS:
                counts["raw"] += 1
            elif ext in IMAGE_EXTENSIONS:
                counts["photos"] += 1

        if counts["photos"]:
            self._make_chip(self.chip_frame, f"Photos  {counts['photos']}", ACCENT)
        if counts["videos"]:
            self._make_chip(self.chip_frame, f"Videos  {counts['videos']}", VIDEO_COLOR)
        if counts["raw"]:
            self._make_chip(self.chip_frame, f"RAW  {counts['raw']}", RAW_COLOR)

    def _set_file_list_text(self, text):
        self.file_textbox.configure(state="normal")
        self.file_textbox.delete("1.0", "end")
        self.file_textbox.insert("1.0", text)
        self.file_textbox.configure(state="disabled")

    # ---- Copy ----

    def _start_copy(self):
        if not self.scanned_files or self.copying:
            return

        base = self.base_folder_var.get()
        if not Path(base).is_dir():
            messagebox.showwarning("Invalid Folder", "The base destination folder does not exist.")
            return

        dest = get_destination(
            self.dest_mode_var.get(), base, self.folder_name_entry.get(),
        )

        confirm = messagebox.askyesno(
            "Confirm Copy",
            f"Copy {len(self.scanned_files)} files to:\n{dest}\n\nContinue?",
        )
        if not confirm:
            return

        ensure_folder(dest)
        self.copying = True
        self.copy_btn.configure(state="disabled", text="Copying...")
        self.scan_btn.configure(state="disabled")
        self.progress_bar.set(0)
        self.progress_pct.configure(text="0%")
        self.progress_label.configure(text="Starting...")
        self.footer.configure(text="Copying files...")

        threading.Thread(target=self._copy_thread, args=(dest,), daemon=True).start()

    def _copy_thread(self, dest):
        def progress_cb(current, total, filename):
            self.after(0, self._update_progress, current, total, filename)

        success, failed, failed_files = copy_files(self.scanned_files, dest, progress_cb)
        self.after(0, self._copy_done, dest, success, failed, failed_files)

    def _update_progress(self, current, total, filename):
        self.progress_bar.set(current / total)
        pct = round(current / total * 100)
        self.progress_pct.configure(text=f"{pct}%")
        short = filename if len(filename) < 40 else "..." + filename[-37:]
        self.progress_label.configure(text=f"[{current}/{total}]  {short}")
        self.footer.configure(text=f"Copying {current}/{total}: {short}")

    def _copy_done(self, dest, success, failed, failed_files):
        self.copying = False
        self.copy_btn.configure(state="normal", text="Copy Files")
        self.scan_btn.configure(state="normal")
        self.progress_bar.set(1)
        done_msg = f"Copied {success} files to {dest}" if success else "Nothing was copied"
        self.progress_label.configure(text=done_msg)
        self.progress_pct.configure(text="100%" if success else "0%")
        self.footer.configure(text="Copy complete")

        msg = f"Successfully copied {success} files to:\n{dest}"
        if failed:
            msg += f"\n\n{failed} file(s) failed:"
            for path, err in failed_files[:10]:
                msg += f"\n- {path.name}: {err}"

        messagebox.showinfo("Copy Complete", msg)

    # ---- Onboarding ----

    def _check_onboarding(self):
        if not settings.get("onboarding_done"):
            self.withdraw()
            onboarding = OnboardingWindow(self)
            self.wait_window(onboarding)

            if onboarding.result:
                settings.save_settings(onboarding.result)
                self.base_folder_var.set(onboarding.result["base_folder"])

            # Smooth transition: fade in main window
            self.deiconify()
            self.attributes("-alpha", 0.0)
            self._fade_in()

    def _fade_in(self, alpha=0.0):
        """Gradually increase window opacity for smooth transition."""
        if alpha < 1.0:
            alpha += 0.08
            self.attributes("-alpha", min(alpha, 1.0))
            self.after(20, self._fade_in, alpha)
        else:
            self.attributes("-alpha", 1.0)

    def _load_saved_settings(self):
        saved = settings.load_settings()
        self.base_folder_var.set(saved.get("base_folder", str(Path.home() / "Pictures")))
        self.mode_var.set(saved.get("default_mode", "all"))
        self.dest_mode_var.set(saved.get("default_dest_mode", "date"))
        self._toggle_dest_name()

        # Apply theme
        theme = saved.get("theme", "system")
        self.theme_var.set(theme)
        self._set_theme(theme)
