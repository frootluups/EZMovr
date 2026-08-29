"""CustomTkinter GUI for the EZMovr."""

import os
import queue
import threading
import time
import customtkinter as ctk
from pathlib import Path
from tkinter import filedialog, messagebox

from PIL import Image

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
from .theme_detector import apply_theme
from .onboarding import OnboardingWindow

ACCENT = "#3B82F6"
ACCENT_HOVER = "#2563EB"
SUCCESS = "#22C55E"
WARN = "#F59E0B"
EMPTY_MSG = "Insert an SD card and click Scan to begin."
THUMB_SIZE = (120, 90)
THUMB_PAD = 6
COLS = 3


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
        self._row_frames = []
        self._row_count = 0

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
        self._rendered = 0
        self._loading_label = None

    def load_files(self, files):
        self.clear()
        self._total = len(files)
        if not files:
            ctk.CTkLabel(self, text="No previews available", text_color="gray50").pack(pady=30)
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

        # Group files into category buckets so sections render contiguously.
        buckets = {cat: [] for cat in self.SECTION_ORDER}
        for f in files:
            if self._gen_id != gen:
                return
            cat = self._categorize(f)
            buckets.setdefault(cat, []).append(f)

        batch = []
        for cat in self.SECTION_ORDER:
            for f in buckets.get(cat, []):
                if self._gen_id != gen:
                    return
                kind, pil = self._classify(f)
                batch.append((cat, f.name, kind, pil))
                if len(batch) >= self.BATCH:
                    self._queue.put(batch)
                    batch = []
                    time.sleep(0.005)  # let the UI pump keep pace
        if batch:
            self._queue.put(batch)

    def _categorize(self, path):
        """Return the section a file belongs to."""
        ext = path.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            return "video"
        if ext in RAW_EXTENSIONS:
            return "raw"
        if ext in IMAGE_EXTENSIONS:
            w, h = self._image_size(path)
            return "portrait" if h > w else "landscape"
        return "landscape"

    def _image_size(self, path):
        """Read image dimensions from the header without full decode."""
        try:
            with Image.open(path) as img:
                return img.size
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
            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
            img = img.convert("RGB")
            out = img.copy()
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
            img = Image.fromarray(rgb)
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
            for category, name, kind, pil in items:
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

    def _start_section(self, category):
        self._row_frames.clear()
        self._row_count = 0
        ctk.CTkLabel(
            self,
            text=self.SECTION_LABELS.get(category, category.title()),
            font=("", 13, "bold"),
            text_color=("gray15", "gray85"),
            anchor="w",
        ).pack(fill="x", padx=2, pady=(10, 2))

    def _add_thumb(self, category, name, kind, pil):
        if category != self._current_section:
            self._current_section = category
            self._start_section(category)

        col = self._row_count % COLS
        if col == 0:
            row = ctk.CTkFrame(self, fg_color="transparent")
            row.pack(fill="x", pady=THUMB_PAD)
            self._row_frames.append(row)
        parent = self._row_frames[-1]

        cell = ctk.CTkFrame(parent, fg_color="transparent")
        cell.pack(side="left", padx=THUMB_PAD)
        self._row_count += 1

        card = ctk.CTkFrame(cell, fg_color=("gray88", "#252525"), corner_radius=6)
        card.pack()

        if kind == "img":
            ctk_img = ctk.CTkImage(
                light_image=pil, dark_image=pil, size=(pil.width, pil.height)
            )
            lbl = ctk.CTkLabel(card, image=ctk_img, text="")
            lbl.image = ctk_img  # prevent GC
            lbl.pack(padx=4, pady=(4, 0))
            self._thumbs.append(ctk_img)
        else:
            label = {
                "video": "Video",
                "raw": "RAW",
                "fail": "Cannot preview",
                "other": "File",
            }.get(kind, "File")
            ctk.CTkLabel(
                card, text=label, font=("", 12, "bold"),
                width=THUMB_SIZE[0], height=THUMB_SIZE[1],
                fg_color=("gray75", "#333"), corner_radius=4,
                text_color=("gray30", "gray75"),
            ).pack(padx=4, pady=(4, 0))

        short = name if len(name) < 18 else name[:15] + "..."
        ctk.CTkLabel(card, text=short, font=("Consolas", 9), text_color="gray70").pack(
            padx=4, pady=(0, 4),
        )


class SDMoverApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("EZMovr")
        self.geometry("1050x700")
        self.minsize(900, 600)

        ctk.set_default_color_theme("blue")

        # Match the root background to the theme colors so maximized/resized
        # areas repaint instantly instead of flashing black.
        self.configure(fg_color=("#ebebeb", "#242424"))
        self.bind("<Configure>", self._on_window_resize)

        self.drives = []
        self.selected_drive = None
        self.scanned_files = []
        self.copying = False

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
        header.pack(fill="x", padx=20, pady=(14, 6))

        ctk.CTkLabel(
            header, text="EZMovr",
            font=("", 20, "bold"),
        ).pack(side="left")

        ctk.CTkLabel(
            header,
            text="Transfer photos and videos from your camera to your PC.",
            font=("", 11), text_color="gray55",
        ).pack(side="left", padx=14)

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
            values=["system", "light", "dark"],
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            command=self._on_theme_change,
            font=("", 11),
            width=200,
            height=30,
        )
        self.theme_menu.pack(side="left")

        # --- Two-column body ---
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._build_left_panel(left)
        self._build_right_panel(right)

    # ---- Left panel ----

    def _build_left_panel(self, parent):
        # Drive
        self._drive_section(parent)
        # Settings
        self._settings_section(parent)
        # Scan button
        scan_frame = ctk.CTkFrame(parent, fg_color="transparent")
        scan_frame.pack(fill="x", pady=(8, 4))

        self.scan_btn = ctk.CTkButton(
            scan_frame, text="Scan SD Card", width=150, height=34,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._start_scan,
        )
        self.scan_btn.pack(side="left")

        self.scan_status = ctk.CTkLabel(
            scan_frame, text="", font=("", 11), text_color="gray55",
        )
        self.scan_status.pack(side="left", padx=12)

        # File list
        list_frame = ctk.CTkFrame(parent)
        list_frame.pack(fill="both", expand=True, pady=(4, 0))

        top = ctk.CTkFrame(list_frame, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 0))

        ctk.CTkLabel(top, text="Files", font=("", 14, "bold")).pack(side="left")
        self.file_count_label = ctk.CTkLabel(
            top, text="", font=("", 11), text_color="gray55",
        )
        self.file_count_label.pack(side="right")

        self.file_textbox = ctk.CTkTextbox(
            list_frame, state="disabled", font=("Consolas", 11),
            fg_color=("gray92", "#1a1a1a"),
        )
        self.file_textbox.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self._set_placeholder()

        # Progress + Copy
        bottom = ctk.CTkFrame(parent, fg_color="transparent")
        bottom.pack(fill="x", pady=(6, 0))

        self.progress_bar = ctk.CTkProgressBar(bottom)
        self.progress_bar.pack(fill="x")
        self.progress_bar.set(0)

        row = ctk.CTkFrame(bottom, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))

        self.progress_label = ctk.CTkLabel(
            row, text="", font=("", 10), text_color="gray55",
        )
        self.progress_label.pack(side="left")

        self.copy_btn = ctk.CTkButton(
            row, text="Copy Files", width=130, height=34, state="disabled",
            fg_color=SUCCESS, hover_color="#16A34A",
            command=self._start_copy,
        )
        self.copy_btn.pack(side="right")

    # ---- Right panel (preview) ----

    def _build_right_panel(self, parent):
        self.preview = PhotoPreviewPanel(
            parent,
            fg_color=("gray90", "#1e1e1e"),
            corner_radius=8,
        )
        self.preview.pack(fill="both", expand=True)

    # ---- Drive section ----

    def _drive_section(self, parent):
        frame = ctk.CTkFrame(parent)
        frame.pack(fill="x", pady=(0, 4))

        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 0))

        ctk.CTkLabel(top, text="SD Card", font=("", 13, "bold")).pack(side="left")
        self.drive_status = ctk.CTkLabel(
            top, text="", font=("", 10), text_color=WARN,
        )
        self.drive_status.pack(side="right")

        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(6, 10))

        self.drive_var = ctk.StringVar(value="No drives found")
        self.drive_menu = ctk.CTkOptionMenu(
            row, variable=self.drive_var,
            values=["No drives found"], width=320,
            fg_color=("gray75", "#2b2b2b"), button_color=ACCENT,
        )
        self.drive_menu.pack(side="left")

        ctk.CTkButton(
            row, text="Refresh", width=70, height=28,
            fg_color=("gray65", "#3a3a3a"), hover_color=("gray55", "#4a4a4a"),
            command=self._scan_drives,
        ).pack(side="left", padx=8)

    # ---- Settings section ----

    def _settings_section(self, parent):
        frame = ctk.CTkFrame(parent)
        frame.pack(fill="x", pady=(0, 4))

        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(inner, text="Copy mode", font=("", 12, "bold")).grid(
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

        ctk.CTkLabel(inner, text="Destination", font=("", 12, "bold")).grid(
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
            dest_row, placeholder_text="Folder name", width=160, state="disabled",
        )
        self.folder_name_entry.pack(side="left")

        base_row = ctk.CTkFrame(inner, fg_color="transparent")
        base_row.grid(row=4, column=0, sticky="w")

        ctk.CTkLabel(base_row, text="Save to:", font=("", 11)).pack(side="left")
        self.base_folder_var = ctk.StringVar(value=str(Path.home() / "Pictures"))
        ctk.CTkEntry(
            base_row, textvariable=self.base_folder_var, width=330,
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            base_row, text="Browse", width=65, height=26,
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

    def _on_theme_change(self, choice):
        apply_theme(choice)
        settings.set("theme", choice)

    def _scan_drives(self):
        self.drives = get_removable_drives()
        if not self.drives:
            self.drive_menu.configure(values=["No removable drives found"])
            self.drive_var.set("No removable drives found")
            self.drive_status.configure(text="Insert an SD card", text_color=WARN)
            return

        labels = [format_drive_info(d) for d in self.drives]
        self.drive_menu.configure(values=labels)
        self.drive_var.set(labels[0])
        self.selected_drive = self.drives[0]
        self.drive_status.configure(text=f"{len(self.drives)} found", text_color=SUCCESS)

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

        if files:
            summary = get_files_summary(files)
            self._set_file_list_text(summary)
            self.file_count_label.configure(text=f"{len(files)} files")
            self.copy_btn.configure(state="normal")
            self.preview.load_files(files)
        else:
            self._set_placeholder()
            self.file_count_label.configure(text="")
            self.copy_btn.configure(state="disabled")
            self.preview.clear()

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

        threading.Thread(target=self._copy_thread, args=(dest,), daemon=True).start()

    def _copy_thread(self, dest):
        def progress_cb(current, total, filename):
            self.after(0, self._update_progress, current, total, filename)

        success, failed, failed_files = copy_files(self.scanned_files, dest, progress_cb)
        self.after(0, self._copy_done, dest, success, failed, failed_files)

    def _update_progress(self, current, total, filename):
        self.progress_bar.set(current / total)
        short = filename if len(filename) < 40 else "..." + filename[-37:]
        self.progress_label.configure(text=f"[{current}/{total}]  {short}")

    def _copy_done(self, dest, success, failed, failed_files):
        self.copying = False
        self.copy_btn.configure(state="normal", text="Copy Files")
        self.scan_btn.configure(state="normal")
        self.progress_bar.set(1)
        self.progress_label.configure(text=f"Copied {success} files to {dest}")

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
        apply_theme(theme)
