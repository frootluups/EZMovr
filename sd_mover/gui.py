"""CustomTkinter GUI for the EZMovr."""

import concurrent.futures
import hashlib
import os
import queue
import sys
import tempfile
import threading
import time
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageOps

try:
    import piexif  # noqa: F401
except Exception:
    piexif = None  # type: ignore

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
from .file_copier import copy_file, copy_files, write_rating
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
LARGE_PREVIEW_MAX = (960, 640)
THUMB_PAD = 6
COLS = 3
THUMB_WORKERS = 1
_THUMB_CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "EZMovr" / "thumb_cache"
try:
    _THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    _THUMB_CACHE_DIR = Path(tempfile.gettempdir()) / "EZMovr_thumb_cache"
    _THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

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


class LargePreviewWindow(ctk.CTkToplevel):
    """Modal window that shows a single file at near-full size."""

    def __init__(self, master, panel, start_idx=0):
        super().__init__(master)
        self.panel = panel
        self.idx = start_idx
        self._img_ref = None

        self.title("Preview")
        self.geometry("980x720")
        self.minsize(640, 480)
        self.transient(master)
        # Match panel theme
        try:
            bg = OLED_BG if getattr(panel, "_oled", False) else ("gray92", "#1f1f1f")
            self.configure(fg_color=bg)
        except Exception:
            pass
        self._center_on_master()

        self._build_ui()
        self._show_current()

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Left>", lambda e: self._prev())
        self.bind("<Right>", lambda e: self._next())
        self.bind("<Up>", lambda e: self._prev())
        self.bind("<Down>", lambda e: self._next())
        self.bind("<Key>", self._on_key)
        self.focus_set()
        try:
            self.grab_set()
        except Exception:
            pass
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # keep panel ref in sync
        try:
            self.panel._viewer = self
        except Exception:
            pass

    def _center_on_master(self):
        try:
            self.update_idletasks()
            mx = self.master.winfo_x()
            my = self.master.winfo_y()
            mw = self.master.winfo_width()
            mh = self.master.winfo_height()
            ww = 980
            wh = 720
            x = max(mx + (mw - ww) // 2, 0)
            y = max(my + (mh - wh) // 2, 0)
            self.geometry(f"{ww}x{wh}+{x}+{y}")
        except Exception:
            pass

    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))

        self.name_label = ctk.CTkLabel(top, text="", font=("", 13, "bold"), anchor="w")
        self.name_label.pack(side="left", fill="x", expand=True)

        self.counter_label = ctk.CTkLabel(top, text="", font=("", 11), text_color="gray55")
        self.counter_label.pack(side="right", padx=8)

        ctk.CTkButton(
            top, text="Close", width=70, height=28,
            fg_color=("gray65", "#3a3a3a"), hover_color=("gray55", "#4a4a4a"),
            command=self._on_close,
        ).pack(side="right", padx=4)

        # Image area — expands and centers the picture
        self.img_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.img_frame.pack(fill="both", expand=True, padx=12, pady=6)

        self.img_label = ctk.CTkLabel(self.img_frame, text="Loading...", font=("", 13))
        self.img_label.pack(expand=True)

        # Star rating row (click to rate current image)
        self.star_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.star_frame.pack(pady=4)
        self.star_labels = []
        for i in range(1, 6):
            lbl = ctk.CTkLabel(self.star_frame, text="☆", font=("", 20), text_color="gray60", width=28)
            lbl.pack(side="left", padx=2)
            # capture i correctly
            lbl.bind("<Button-1>", lambda e, r=i: self._on_star(r), add="+")
            try:
                if hasattr(lbl, "_canvas") and lbl._canvas is not None:
                    lbl._canvas.bind("<Button-1>", lambda e, r=i: self._on_star(r), add="+")
            except Exception:
                pass
            self.star_labels.append(lbl)

        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.pack(fill="x", padx=12, pady=(4, 12))

        self.prev_btn = ctk.CTkButton(nav, text="< Prev", width=100, command=self._prev)
        self.prev_btn.pack(side="left")

        ctk.CTkLabel(nav, text="  Use Left / Right arrows, Esc to close  ",
                     font=("", 10), text_color="gray55").pack(side="left", expand=True)

        self.next_btn = ctk.CTkButton(nav, text="Next >", width=100, command=self._next)
        self.next_btn.pack(side="right")

    def _on_close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            if getattr(self.panel, "_viewer", None) is self:
                self.panel._viewer = None
        except Exception:
            pass
        self.destroy()

    def show_at(self, idx):
        n = len(getattr(self.panel, "_preview_items", []))
        if not n:
            return
        self.idx = max(0, min(idx, n - 1))
        self._show_current()
        self.lift()

    def _prev(self):
        if self.idx > 0:
            self.idx -= 1
            self._show_current()

    def _next(self):
        n = len(getattr(self.panel, "_preview_items", []))
        if self.idx + 1 < n:
            self.idx += 1
            self._show_current()

    def _on_key(self, event):
        # 1-5 rate, 0 clears
        if event.char and event.char in "12345":
            self._on_star(int(event.char))
            return "break"
        if (event.char and event.char == "0") or event.keysym in ("0", "KP_0"):
            try:
                path, _k = self.panel._preview_items[self.idx]
                cur = self.panel.get_rating(path)
                if cur != 0:
                    self.panel.set_rating(path, cur)
                    self._refresh_stars()
            except Exception:
                pass
            return "break"
        if event.keysym in ("Left", "Up"):
            self._prev()
            return "break"
        if event.keysym in ("Right", "Down"):
            self._next()
            return "break"
        if event.keysym == "Escape":
            self._on_close()
            return "break"

    def _show_current(self):
        items = getattr(self.panel, "_preview_items", [])
        if not items or not (0 <= self.idx < len(items)):
            return
        path, kind = items[self.idx]
        self.name_label.configure(text=path.name)
        self.counter_label.configure(text=f"{self.idx + 1} / {len(items)}")

        # Nav state
        try:
            self.prev_btn.configure(state="normal" if self.idx > 0 else "disabled")
            self.next_btn.configure(state="normal" if self.idx + 1 < len(items) else "disabled")
        except Exception:
            pass

        if kind == "img":
            pil = self.panel._load_large_preview(path)
            if pil is not None:
                self._img_ref = ctk.CTkImage(light_image=pil, dark_image=pil,
                                             size=(pil.width, pil.height))
                self.img_label.configure(image=self._img_ref, text="")
                self._refresh_stars()
                return

        # Video / RAW without thumb / other / load failed -> large placeholder
        badge, subtitle = {
            "video": (VIDEO_COLOR, "Video"),
            "raw": (RAW_COLOR, "RAW"),
            "fail": (DANGER, "Cannot preview"),
            "other": ("gray55", "File"),
        }.get(kind, ("gray55", "File"))
        # Reuse the image label as a large badge
        self._img_ref = None
        self.img_label.configure(image="", text=f"{badge_text(kind)}\n{subtitle}",
                                 font=("", 28, "bold"), text_color=badge)
        self._refresh_stars()

    def _on_star(self, rating):
        try:
            path, _k = self.panel._preview_items[self.idx]
            self.panel.set_rating(path, rating)
            self._refresh_stars()
        except Exception:
            pass

    def _refresh_stars(self):
        try:
            path, _k = self.panel._preview_items[self.idx]
            cur = self.panel.get_rating(path)
        except Exception:
            cur = 0
        for i, lbl in enumerate(getattr(self, "star_labels", [])):
            filled = (i + 1) <= cur
            lbl.configure(text="★" if filled else "☆",
                          text_color="#FACC15" if filled else "gray60")


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
        self._preview_items = []  # [(Path, kind)] in display order
        self._ratings = {}  # {str(Path): 0-5}
        self._card_map = {}  # {str(Path): card}
        self._selected = set()  # {str(Path)}
        self._filter = "all"
        self._large_cache = {}  # {str(Path): PIL}
        self._large_cache_order = []
        self._viewer = None
        self._relayout_pending = False
        self._last_relayout_width = 0
        self._oled = False
        self.bind("<Configure>", self._on_resize)
        self.bind("<Control-a>", lambda e: (self.select_all(), "break")[1])
        self.bind("<Control-A>", lambda e: (self.select_all(), "break")[1])
        # Trackpad: ensure small deltas scroll (also handle scroll over cards)
        self._parent_canvas.bind("<MouseWheel>", self._on_mouse_wheel, add=True)
        self.bind("<MouseWheel>", self._on_mouse_wheel, add=True)
        if not sys.platform.startswith("win"):
            self.bind("<Button-4>", lambda e: self._on_mouse_wheel_linux(-1), add=True)
            self.bind("<Button-5>", lambda e: self._on_mouse_wheel_linux(1), add=True)

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

    # ---- thumb cache (disk) — direct from card, then cached locally ----

    def _thumb_cache_key(self, path):
        try:
            st = path.stat()
            raw = f"{path}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8", errors="ignore")
        except Exception:
            raw = str(path).encode("utf-8", errors="ignore")
        return hashlib.md5(raw).hexdigest() + ".jpg"

    def _load_cached_thumb(self, path):
        try:
            p = _THUMB_CACHE_DIR / self._thumb_cache_key(path)
            if p.is_file():
                # cache hit — load directly from local disk (faster than SD)
                img = Image.open(p)
                img.load()
                # ensure it fits thumb size (cache already thumb-sized)
                return img.convert("RGB")
        except Exception:
            pass
        return None

    def _save_thumb_cache(self, path, pil):
        try:
            p = _THUMB_CACHE_DIR / self._thumb_cache_key(path)
            # save as JPEG, small & fast
            pil.save(p, "JPEG", quality=85)
        except Exception:
            pass

    def _load_large_preview(self, path):
        key = str(path)
        if key in self._large_cache:
            try:
                self._large_cache_order.remove(key)
            except ValueError:
                pass
            self._large_cache_order.append(key)
            return self._large_cache[key]
        ext = path.suffix.lower()
        out = None
        if ext in RAW_EXTENSIONS:
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
                img.thumbnail(LARGE_PREVIEW_MAX, Image.BILINEAR)
                out = img.convert("RGB")
            except Exception:
                return None
        elif ext in VIDEO_EXTENSIONS:
            return None
        elif ext in IMAGE_EXTENSIONS:
            img = None
            try:
                img = Image.open(path)
                img.draft("RGB", LARGE_PREVIEW_MAX)
                try:
                    orient = img.getexif().get(0x0112, 1)
                except Exception:
                    orient = 1
                if orient not in (1, None, 0):
                    img = ImageOps.exif_transpose(img)
                img.thumbnail(LARGE_PREVIEW_MAX, Image.BILINEAR)
                out = img.convert("RGB").copy()
            except Exception:
                return None
            finally:
                if img is not None:
                    try:
                        img.close()
                    except Exception:
                        pass
        if out is not None:
            self._large_cache[key] = out
            self._large_cache_order.append(key)
            if len(self._large_cache_order) > 24:
                oldest = self._large_cache_order.pop(0)
                self._large_cache.pop(oldest, None)
        return out

    def open_viewer(self, path):
        idx = 0
        for i, (p, _k) in enumerate(self._preview_items):
            if p == path:
                idx = i
                break
        if getattr(self, "_viewer", None) is not None:
            try:
                if self._viewer.winfo_exists():
                    self._viewer.show_at(idx)
                    self._viewer.lift()
                    self._viewer.focus_set()
                    return
            except Exception:
                pass
            self._viewer = None
        try:
            self._viewer = LargePreviewWindow(self.winfo_toplevel(), self, idx)
        except Exception:
            # If transient/grab fails, try without grab
            self._viewer = LargePreviewWindow(self.winfo_toplevel(), self, idx)

    def _bind_card_click(self, widget, path):
        if getattr(widget, "_is_star", False):
            return
        cb = lambda e, p=path: self.open_viewer(p)
        for w in (widget, getattr(widget, "_canvas", None), getattr(widget, "_label", None)):
            if w is None:
                continue
            if getattr(w, "_is_star", False):
                continue
            try:
                w.bind("<Button-1>", cb, add="+")
            except Exception:
                pass
            try:
                w.configure(cursor="hand2")
            except Exception:
                pass
        for child in widget.winfo_children():
            if getattr(child, "_is_star", False):
                continue
            self._bind_card_click(child, path)
        # CTk widgets sometimes keep an inner canvas under a different attr
        for attr in ("_canvas", "_label", "_text_label"):
            inner = getattr(widget, attr, None)
            if inner is not None and inner is not widget and not getattr(inner, "_is_star", False):
                try:
                    inner.bind("<Button-1>", cb, add="+")
                except Exception:
                    pass

    def get_rating(self, path):
        return self._ratings.get(str(path), 0)

    def set_rating(self, path, rating):
        key = str(path)
        cur = self._ratings.get(key, 0)
        if cur == rating:
            rating = 0
        if rating:
            self._ratings[key] = rating
        else:
            self._ratings.pop(key, None)
        card = self._card_map.get(key)
        if card is not None:
            # single-label stars (current) — one widget with 5 chars
            star_label = getattr(card, "_star_label", None)
            if star_label is not None:
                try:
                    star_label.configure(text="★" * rating + "☆" * (5 - rating),
                                         text_color="#FACC15" if rating else "gray60")
                except Exception:
                    pass
            else:
                stars = getattr(card, "_stars", None)
                if stars:
                    for i, lbl in enumerate(stars):
                        n = i + 1
                        filled = n <= rating
                        try:
                            lbl.configure(text="★" if filled else "☆",
                                          text_color="#FACC15" if filled else "gray60")
                        except Exception:
                            pass
        if getattr(self, "_viewer", None) is not None:
            try:
                if self._viewer.winfo_exists():
                    cur_p, _k = self._preview_items[self._viewer.idx]
                    if str(cur_p) == key:
                        self._viewer._refresh_stars()
            except Exception:
                pass
        # re-apply filter if active (rated stars may change visibility)
        try:
            self.apply_filter()
        except Exception:
            pass
        return rating

    def _on_select_toggle(self, path, checked):
        key = str(path)
        if checked:
            self._selected.add(key)
        else:
            self._selected.discard(key)
        card = self._card_map.get(key)
        if card is not None:
            try:
                card.configure(border_color=ACCENT if checked else self._oled_val(CARD_BORDER, OLED_CARD_BORDER))
            except Exception:
                pass

    def toggle_select(self, path):
        key = str(path)
        if key in self._selected:
            self._selected.remove(key)
        else:
            self._selected.add(key)
        card = self._card_map.get(key)
        if card is not None:
            try:
                cb = getattr(card, "_sel_cb", None)
                if cb:
                    # update checkbox without triggering command
                    pass
                # visual cue: border accent when selected
                card.configure(border_color=ACCENT if key in self._selected else self._oled_val(CARD_BORDER, OLED_CARD_BORDER))
            except Exception:
                pass

    def select_all(self):
        for p, _k in self._preview_items:
            self._selected.add(str(p))
        for key, card in self._card_map.items():
            try:
                card.configure(border_color=ACCENT)
                if hasattr(card, "_sel_var"):
                    card._sel_var.set(True)
            except Exception:
                pass

    def clear_selection(self):
        for key in list(self._selected):
            card = self._card_map.get(key)
            if card is not None:
                try:
                    card.configure(border_color=self._oled_val(CARD_BORDER, OLED_CARD_BORDER))
                    if hasattr(card, "_sel_var"):
                        card._sel_var.set(False)
                except Exception:
                    pass
        self._selected.clear()

    def get_selected_files(self):
        # return Path objects for selected, fallback to all if none selected
        if not self._selected:
            return []
        out = []
        for p, _k in self._preview_items:
            if str(p) in self._selected:
                out.append(p)
        return out

    def set_filter(self, mode):
        self._filter = mode
        self.apply_filter()

    def apply_filter(self):
        # show/hide cards based on self._filter
        try:
            thr = 3 if self._filter == "rated3" else 1
        except Exception:
            thr = 1
        for p, kind in self._preview_items:
            key = str(p)
            card = self._card_map.get(key)
            if card is None:
                continue
            rating = self._ratings.get(key, 0)
            show = True
            if self._filter == "rated3":
                show = rating >= 3
            elif self._filter == "unrated":
                show = rating == 0
            elif self._filter == "raw":
                show = kind in ("raw",) or str(p).lower().endswith((".cr2",".cr3",".nef",".arw",".raf",".orf",".rw2",".dng",".pef",".x3f"))
            elif self._filter == "video":
                show = kind == "video"
            # else "all" show True
            try:
                # card is gridded inside its section's content; use grid_remove to hide but keep grid options
                if show:
                    card.grid()
                else:
                    card.grid_remove()
            except Exception:
                try:
                    if show:
                        card.pack()
                    else:
                        card.pack_forget()
                except Exception:
                    pass

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
        self._preview_items = []
        self._ratings.clear()
        self._card_map.clear()
        self._large_cache.clear()
        self._large_cache_order.clear()
        if getattr(self, "_viewer", None) is not None:
            try:
                if self._viewer.winfo_exists():
                    self._viewer.destroy()
            except Exception:
                pass
            self._viewer = None
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

        # Phase 2 — decode thumbnails in parallel, direct from card.
        # Each worker opens its own file (no shared buffer), BILINEAR +
        # EXIF-thumb + disk cache make it ~3-4× faster than sequential.
        def _decode_one(item):
            cat, f = item
            if self._gen_id != gen:
                return None
            kind, pil = self._classify(f)
            return (cat, f, kind, pil)

        batch = []
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=THUMB_WORKERS) as ex:
                for result in ex.map(_decode_one, categorized):
                    if self._gen_id != gen:
                        ex.shutdown(wait=False, cancel_futures=True)
                        return
                    if result is None:
                        continue
                    batch.append(result)
                    if len(batch) >= self.BATCH:
                        self._queue.put(batch)
                        batch = []
                        time.sleep(0.003)
        except Exception:
            # fallback to sequential if thread pool fails
            batch = []
            for cat, f in categorized:
                if self._gen_id != gen:
                    return
                kind, pil = self._classify(f)
                batch.append((cat, f, kind, pil))
                if len(batch) >= self.BATCH:
                    self._queue.put(batch)
                    batch = []
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
        # 0) local disk cache — no SD access at all
        cached = self._load_cached_thumb(path)
        if cached is not None:
            return cached

        # 1) embedded EXIF thumbnail (tiny JPEG stored by camera, ~10KB)
        try:
            import piexif
            exif_dict = piexif.load(str(path))
            thumb = exif_dict.get("thumbnail")
            if thumb:
                img = Image.open(BytesIO(thumb))
                img.thumbnail(THUMB_SIZE, Image.BILINEAR)
                out = img.convert("RGB")
                threading.Thread(target=self._save_thumb_cache, args=(path, out), daemon=True).start()
                return out
        except Exception:
            pass

        # 2) direct card access — draft + BILINEAR, skip transpose if not needed
        img = None
        try:
            img = Image.open(path)
            img.draft("RGB", THUMB_SIZE)  # JPEG fast-path, read directly from card
            try:
                orient = img.getexif().get(0x0112, 1)
            except Exception:
                orient = 1
            if orient not in (1, None, 0):
                img = ImageOps.exif_transpose(img)
            img.thumbnail(THUMB_SIZE, Image.BILINEAR)
            out = img.convert("RGB").copy()
            self._save_thumb_cache(path, out)
            return out
        except Exception:
            return None
        finally:
            if img is not None:
                try:
                    img.close()
                except Exception:
                    pass

    def _load_raw_pil(self, path):
        """Render a RAW preview via rawpy (CR2, NEF, ARW, DNG, etc.)."""
        cached = self._load_cached_thumb(path)
        if cached is not None:
            return cached
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
            img.thumbnail(THUMB_SIZE, Image.BILINEAR)
            out = img.convert("RGB")
            self._save_thumb_cache(path, out)
            return out
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
                try:
                    category, path, kind, pil = item
                    self._add_thumb(category, path, kind, pil)
                except Exception as e:
                    print(f"thumb failed for {item}: {e}")
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
        if event.widget is not self or self._relayout_pending or getattr(self, "_in_relayout", False):
            return
        # Only relayout on actual width changes — repacking thumbnails fires
        # Configure events too, and relayouting on those would loop forever.
        if self.winfo_width() == self._last_relayout_width:
            return
        self._relayout_pending = True
        self.after(120, self._do_relayout)

    def _on_mouse_wheel(self, event):
        if not self._check_if_valid_scroll(event.widget):
            return
        try:
            delta = int(event.delta)
        except Exception:
            delta = 0
        if delta == 0:
            return "break"
        units = -int(delta / 30)
        if units == 0:
            units = -1 if delta > 0 else 1
        if abs(units) > 6:
            units = 6 if units > 0 else -6
        try:
            if getattr(self, "_shift_pressed", False):
                if self._parent_canvas.xview() != (0.0, 1.0):
                    self._parent_canvas.xview_scroll(units, "units")
            else:
                if self._parent_canvas.yview() != (0.0, 1.0):
                    self._parent_canvas.yview_scroll(units, "units")
        except Exception:
            pass
        return "break"

    def _on_mouse_wheel_linux(self, delta):
        try:
            if self._parent_canvas.yview() != (0.0, 1.0):
                self._parent_canvas.yview_scroll(delta, "units")
        except Exception:
            pass
        return "break"

    def _do_relayout(self):
        self._relayout_pending = False
        self._relayout()

    def _relayout(self):
        if not self._sections:
            return
        if getattr(self, "_in_relayout", False):
            return
        self._in_relayout = True
        try:
            self._last_relayout_width = self.winfo_width()
            cols = self._compute_cols()
            self._relayout_impl(cols)
            try:
                self._parent_canvas.configure(scrollregion=self._parent_canvas.bbox("all"))
            except Exception:
                pass
        finally:
            self._in_relayout = False

    def _relayout_impl(self, cols):
        # Forget headers and content, then re-grid with new column count
        for sec in self._sections:
            if sec.get("header") is not None:
                try:
                    sec["header"].pack_forget()
                    sec["header"].destroy()
                except Exception:
                    pass
                sec["header"] = None
            try:
                sec["content"].pack_forget()
            except Exception:
                pass
            for card in sec["cards"]:
                try:
                    card.grid_forget()
                except Exception:
                    try:
                        card.pack_forget()
                    except Exception:
                        pass
        for r in list(self._row_frames):
            try:
                r.destroy()
            except Exception:
                pass
        self._row_frames = []
        self._current_section = None

        for sec in self._sections:
            sec["header"] = self._make_header(sec["cat"])
            sec["header"].pack(fill="x", padx=2, pady=(12, 4))
            sec["content"].pack(fill="x", padx=2, pady=(0, 8))
            for c in range(cols):
                try:
                    sec["content"].grid_columnconfigure(c, weight=1)
                except Exception:
                    pass
            for i, card in enumerate(sec["cards"]):
                row = i // cols
                col = i % cols
                try:
                    card.grid(in_=sec["content"], row=row, column=col, padx=THUMB_PAD, pady=THUMB_PAD, sticky="nsew")
                except Exception:
                    try:
                        card.pack(in_=sec["content"])
                    except Exception:
                        pass
        self._row_count = sum(len(s["cards"]) for s in self._sections)
        self._current_section = self._sections[-1]["cat"] if self._sections else None

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
        header = self._make_header(category)
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="x", padx=2, pady=(0, 8))
        cols = self._compute_cols()
        for c in range(cols):
            content.grid_columnconfigure(c, weight=1)
        self._sections.append({"cat": category, "cards": [], "header": header, "content": content, "grid_cols": cols})

    def _add_thumb(self, category, path, kind, pil):
        if isinstance(path, str):
            path = Path(path)
        if category != self._current_section:
            self._start_section(category)

        sec = self._sections[-1]
        content = sec["content"]
        cols = self._compute_cols()
        for c in range(cols):
            try:
                content.grid_columnconfigure(c, weight=1)
            except Exception:
                pass
        sec["grid_cols"] = cols
        idx = len(sec["cards"])
        row = idx // cols
        col = idx % cols
        try:
            card = self._build_card(path, kind, pil)
            card.grid(in_=content, row=row, column=col, padx=THUMB_PAD, pady=THUMB_PAD, sticky="nsew")
            sec["cards"].append(card)
            self._preview_items.append((path, kind))
            self._card_map[str(path)] = card
            self._bind_card_click(card, path)
        except Exception as e:
            try:
                self._preview_items.append((path, kind))
            except Exception:
                pass
            print(f"card failed for {path}: {e}")

    def _on_star_click(self, path, rating):
        self.set_rating(path, rating)
        return "break"

    def _build_card(self, path, kind, pil):
        name = path.name if hasattr(path, "name") else str(path)
        card = ctk.CTkFrame(
            self,
            fg_color=self._oled_val(CARD_FG, OLED_CARD_FG),
            corner_radius=8,
            border_width=1,
            border_color=self._oled_val(CARD_BORDER, OLED_CARD_BORDER),
        )
        card._is_card = True
        # selection checkbox (top-right)
        sel_var = ctk.BooleanVar(value=str(path) in self._selected)
        cb = ctk.CTkCheckBox(card, text="", variable=sel_var, width=18, height=18, checkbox_width=18, checkbox_height=18, border_width=1, command=lambda p=path, v=sel_var: self._on_select_toggle(p, v.get()))
        cb._is_star = True  # prevent card click binding
        cb.pack(anchor="ne", padx=4, pady=(4, 0))
        card._sel_var = sel_var
        card._sel_cb = cb
        # also handle the checkbox's internal canvas
        try:
            if hasattr(cb, "_canvas"):
                cb._canvas._is_star = True
        except Exception:
            pass
        card.bind("<Enter>", lambda e, c=card: c.configure(border_color=ACCENT if str(path) not in self._selected else "#3B82F6"))
        card.bind(
            "<Leave>",
            lambda e, c=card: c.configure(
                border_color=self._oled_val(CARD_BORDER, OLED_CARD_BORDER) if str(path) not in self._selected else ACCENT
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
            padx=4, pady=(0, 2),
        )
        # single-label star row (5 stars in one widget — ~5× fewer widgets)
        cur = self.get_rating(path)
        star_label = ctk.CTkLabel(
            card, text="★" * cur + "☆" * (5 - cur), font=("", 13),
            text_color="#FACC15" if cur else "gray60",
        )
        star_label._is_star = True
        card._path = str(path)

        def _star_click(e):
            try:
                w = star_label.winfo_width()
                if w <= 1:
                    w = 80
                rel = e.x / w
                rel = max(0.0, min(1.0, rel))
                r = int(rel * 5) + 1
                if r < 1:
                    r = 1
                if r > 5:
                    r = 5
                self._on_star_click(path, r)
            except Exception:
                self._on_star_click(path, 3)
            return "break"

        star_label.bind("<Button-1>", _star_click, add="+")
        for attr in ("_canvas", "_label"):
            inner = getattr(star_label, attr, None)
            if inner is not None:
                try:
                    inner.bind("<Button-1>", _star_click, add="+")
                except Exception:
                    pass
        star_label.pack(pady=(0, 4))
        card._stars = [star_label]
        card._star_label = star_label
        return card


class SDMoverApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("EZMovr")
        self.geometry("1050x700")
        self.minsize(900, 600)

        # App icon (window + taskbar)
        try:
            import sys as _sys
            base = Path(getattr(_sys, "_MEIPASS", Path(__file__).parent.parent))
            ico = base / "assets" / "icon.ico"
            png = base / "assets" / "icon.png"
            if ico.exists():
                try:
                    self.iconbitmap(str(ico))
                except Exception:
                    pass
                try:
                    from PIL import Image as _PILImage, ImageTk
                    im = _PILImage.open(str(png if png.exists() else ico))
                    im = im.resize((64, 64), _PILImage.LANCZOS)
                    self._icon_img = ImageTk.PhotoImage(im)
                    self.wm_iconphoto(True, self._icon_img)
                except Exception:
                    pass
        except Exception:
            pass

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

    def _scroll_preview(self, delta):
        # Don't hijack when typing in an entry or when large viewer is open
        try:
            if getattr(self.preview, "_viewer", None) and self.preview._viewer.winfo_exists():
                return
        except Exception:
            pass
        foc = self.focus_get()
        if foc is not None:
            try:
                # CTkEntry / CTkTextbox or native Entry/Text should keep arrow keys
                if isinstance(foc, (ctk.CTkEntry, ctk.CTkTextbox)):
                    return
                cname = foc.winfo_class()
                if cname in ("Entry", "Text"):
                    return
                # heuristic for CTk's internal entry canvas
                if "entry" in str(foc).lower():
                    return
            except Exception:
                pass
        try:
            self.preview._parent_canvas.yview_scroll(int(delta), "units")
        except Exception:
            pass

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

        # Arrow keys scroll the preview grid (viewer handles its own keys when open)
        self.bind("<Up>", lambda e: self._scroll_preview(-3))
        self.bind("<Down>", lambda e: self._scroll_preview(3))
        self.bind("<Prior>", lambda e: self._scroll_preview(-12))
        self.bind("<Next>", lambda e: self._scroll_preview(12))

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
        # Filter bar
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(bar, text="Filter:", font=("", 11)).pack(side="left", padx=(0, 4))
        self.filter_var = ctk.StringVar(value="all")
        for label, val in [("All", "all"), ("★≥3", "rated3"), ("Unrated", "unrated"), ("RAW", "raw"), ("Video", "video")]:
            ctk.CTkButton(
                bar, text=label, width=60, height=24,
                command=lambda v=val: self.preview.set_filter(v) if hasattr(self, "preview") else None,
            ).pack(side="left", padx=2)
        sel_bar = ctk.CTkFrame(parent, fg_color="transparent")
        sel_bar.pack(fill="x", pady=(0, 6))
        ctk.CTkButton(sel_bar, text="Select All", width=80, height=24, command=lambda: self.preview.select_all() if hasattr(self, "preview") else None).pack(side="left", padx=2)
        ctk.CTkButton(sel_bar, text="Clear", width=60, height=24, command=lambda: self.preview.clear_selection() if hasattr(self, "preview") else None).pack(side="left", padx=2)
        ctk.CTkButton(sel_bar, text="Copy Selected", width=110, height=24, fg_color=SUCCESS, hover_color=SUCCESS_HOVER, command=self._copy_selected).pack(side="right", padx=2)
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

        ctk.CTkLabel(inner, text="Rating export", font=("", 11, "bold")).grid(
            row=5, column=0, sticky="w", pady=(8, 3),
        )
        rating_row = ctk.CTkFrame(inner, fg_color="transparent")
        rating_row.grid(row=6, column=0, sticky="w")
        self.rating_mode_var = ctk.StringVar(value="metadata")
        ctk.CTkRadioButton(
            rating_row, text="Write to metadata",
            variable=self.rating_mode_var, value="metadata",
            command=lambda: settings.set("rating_mode", self.rating_mode_var.get()),
        ).pack(side="left", padx=(0, 10))
        ctk.CTkRadioButton(
            rating_row, text="Sort into 'rated' folder",
            variable=self.rating_mode_var, value="folder",
            command=lambda: settings.set("rating_mode", self.rating_mode_var.get()),
        ).pack(side="left", padx=(0, 10))
        ctk.CTkRadioButton(
            rating_row, text="Both",
            variable=self.rating_mode_var, value="both",
            command=lambda: settings.set("rating_mode", self.rating_mode_var.get()),
        ).pack(side="left")

        ctk.CTkLabel(inner, text="Copy selection", font=("", 11, "bold")).grid(
            row=7, column=0, sticky="w", pady=(8, 3),
        )
        copy_sel_row = ctk.CTkFrame(inner, fg_color="transparent")
        copy_sel_row.grid(row=8, column=0, sticky="w")
        self.copy_selection_var = ctk.StringVar(value="all")
        ctk.CTkOptionMenu(
            copy_sel_row, variable=self.copy_selection_var, values=["All files", "Selected only"],
            command=lambda v: settings.set("copy_selection", "selected" if "Selected" in v else "all"),
            width=160,
        ).pack(side="left")
        ctk.CTkLabel(inner, text="Rated threshold (for 'rated' folder)", font=("", 11, "bold")).grid(
            row=9, column=0, sticky="w", pady=(8, 3),
        )
        thresh_row = ctk.CTkFrame(inner, fg_color="transparent")
        thresh_row.grid(row=10, column=0, sticky="w")
        self.rating_threshold_var = ctk.IntVar(value=1)
        self.rating_threshold_slider = ctk.CTkSlider(
            thresh_row, from_=1, to=5, number_of_steps=4, width=150, command=self._on_rating_threshold_change,
        )
        self.rating_threshold_slider.pack(side="left")
        self.rating_threshold_label = ctk.CTkLabel(thresh_row, text="≥1 ★", font=("", 11))
        self.rating_threshold_label.pack(side="left", padx=8)
        ctk.CTkLabel(inner, text="Performance", font=("", 11, "bold")).grid(
            row=11, column=0, sticky="w", pady=(8, 3),
        )
        perf_row = ctk.CTkFrame(inner, fg_color="transparent")
        perf_row.grid(row=12, column=0, sticky="w")
        self.verify_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            perf_row, text="Verify after copy", variable=self.verify_var,
            command=lambda: settings.set("verify_after_copy", bool(self.verify_var.get())),
        ).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(perf_row, text="Thumb size:", font=("", 11)).pack(side="left", padx=(0, 4))
        self.thumb_size_var = ctk.StringVar(value="auto")
        ctk.CTkOptionMenu(
            perf_row, variable=self.thumb_size_var, values=["Auto", "Small", "Medium", "Large"],
            command=self._on_thumb_size_change, width=120,
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

    def _on_rating_threshold_change(self, value):
        try:
            v = int(round(float(value)))
            v = max(1, min(5, v))
            self.rating_threshold_var.set(v)
            self.rating_threshold_label.configure(text=f"≥{v} ★")
            settings.set("rating_threshold", v)
        except Exception:
            pass

    def _on_thumb_size_change(self, choice):
        val = str(choice).lower()
        # normalize Auto/S/M/L
        if val == "auto":
            key = "auto"
        elif val in ("small", "s"):
            key = "S"
        elif val in ("large", "l"):
            key = "L"
        else:
            key = "M"
        settings.set("thumb_size", key.lower() if key != "auto" else "auto")
        # update global thumb size for next load
        size_map = {"S": (90, 68), "M": (120, 90), "L": (150, 113)}
        if key != "auto" and key in size_map:
            # set global for next thumbnails (preview will use on next scan)
            import sd_mover.gui as _g
            _g.THUMB_SIZE = size_map[key]
        self.footer.configure(text=f"Thumb size {key} — will apply on next scan")

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

        # respect copy_selection dropdown
        try:
            sel_mode = self.copy_selection_var.get() if hasattr(self, "copy_selection_var") else "all"
        except Exception:
            sel_mode = "all"
        sel_mode = "selected" if "Selected" in str(sel_mode) else "all"
        files_for_confirm = self.scanned_files
        if sel_mode == "selected":
            try:
                sel = set(getattr(self.preview, "_selected", set()))
                if sel:
                    files_for_confirm = [p for p in self.scanned_files if str(p) in sel]
                else:
                    messagebox.showwarning("No Selection", "No files selected. Select files in the preview or switch to 'All files'.")
                    return
            except Exception:
                pass

        confirm = messagebox.askyesno(
            "Confirm Copy",
            f"Copy {len(files_for_confirm)} files to:\n{dest}\n\nContinue?",
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

    def _copy_selected(self):
        if not self.scanned_files or self.copying:
            return
        try:
            sel = set(getattr(self.preview, "_selected", set()))
            if not sel:
                messagebox.showwarning("No Selection", "No files selected. Use checkboxes on previews or Select All.")
                return
            selected = [p for p in self.scanned_files if str(p) in sel]
            if not selected:
                messagebox.showwarning("No Selection", "Selected files not found in scan.")
                return
        except Exception as e:
            messagebox.showwarning("Error", str(e))
            return
        base = self.base_folder_var.get()
        if not Path(base).is_dir():
            messagebox.showwarning("Invalid Folder", "The base destination folder does not exist.")
            return
        dest = get_destination(self.dest_mode_var.get(), base, self.folder_name_entry.get())
        confirm = messagebox.askyesno("Confirm Copy", f"Copy {len(selected)} selected files to:\n{dest}\n\nContinue?")
        if not confirm:
            return
        ensure_folder(dest)
        self.copying = True
        self.copy_btn.configure(state="disabled", text="Copying...")
        self.scan_btn.configure(state="disabled")
        self.progress_bar.set(0)
        self.progress_pct.configure(text="0%")
        self.progress_label.configure(text="Starting...")
        self.footer.configure(text="Copying selected files...")
        # _copy_thread will handle selection via copy_selection, but for this button we force selected
        # Temporarily override the selection set to ensure _copy_thread sees it
        threading.Thread(target=self._copy_thread_selected, args=(dest, selected), daemon=True).start()

    def _copy_thread_selected(self, dest, selected_files):
        def progress_cb(current, total, filename):
            self.after(0, self._update_progress, current, total, filename)
        rating_mode = self.rating_mode_var.get() if hasattr(self, "rating_mode_var") else "metadata"
        try:
            rating_thr = int(self.rating_threshold_var.get()) if hasattr(self, "rating_threshold_var") else 1
        except Exception:
            rating_thr = 1
        verify = bool(self.verify_var.get()) if hasattr(self, "verify_var") else False
        ratings = getattr(self.preview, "_ratings", {})
        def _rating_of(p): return ratings.get(str(p), 0)
        rated_dir = dest / "rated"
        needs_rated = rating_mode in ("folder", "both") and any(_rating_of(p) >= rating_thr for p in selected_files)
        if needs_rated:
            try: ensure_folder(rated_dir)
            except Exception: pass
        total = len(selected_files)
        success = failed = 0
        failed_files = []
        for i, src in enumerate(selected_files, 1):
            try:
                if progress_cb: progress_cb(i, total, src.name)
                r = _rating_of(src)
                dst = copy_file(src, rated_dir if rating_mode in ("folder","both") and r >= rating_thr else dest)
                if verify:
                    from .file_scanner import compute_file_hash
                    if compute_file_hash(src) != compute_file_hash(dst):
                        try: dst.unlink(missing_ok=True)
                        except Exception: pass
                        raise OSError("hash mismatch")
                if rating_mode in ("metadata","both") and r>0:
                    try: write_rating(dst, r)
                    except Exception: pass
                success+=1
            except Exception as e:
                failed+=1; failed_files.append((src,str(e)))
        self.after(0, self._copy_done, dest, success, failed, failed_files)

    def _copy_thread(self, dest):
        def progress_cb(current, total, filename):
            self.after(0, self._update_progress, current, total, filename)

        rating_mode = self.rating_mode_var.get() if hasattr(self, "rating_mode_var") else "metadata"
        try:
            rating_thr = int(self.rating_threshold_var.get()) if hasattr(self, "rating_threshold_var") else 1
        except Exception:
            rating_thr = 1
        verify = bool(self.verify_var.get()) if hasattr(self, "verify_var") else False
        # copy selection: dropdown "Selected only" vs "All"
        try:
            sel_mode = self.copy_selection_var.get() if hasattr(self, "copy_selection_var") else "all"
        except Exception:
            sel_mode = "all"
        sel_mode = "selected" if "Selected" in str(sel_mode) else "all"
        ratings = getattr(self.preview, "_ratings", {})
        # selected set from preview
        try:
            selected = set(getattr(self.preview, "_selected", set()))
        except Exception:
            selected = set()

        def _rating_of(p):
            return ratings.get(str(p), 0)

        # decide which files to copy
        if sel_mode == "selected" and selected:
            files_to_copy = [p for p in self.scanned_files if str(p) in selected]
        else:
            files_to_copy = list(self.scanned_files)

        # folder mode: rated files go to dest/rated/ when rating >= threshold
        rated_dir = dest / "rated"
        needs_rated = rating_mode in ("folder", "both") and any(_rating_of(p) >= rating_thr for p in files_to_copy)
        if needs_rated:
            try:
                ensure_folder(rated_dir)
            except Exception:
                pass

        total = len(files_to_copy)
        if total == 0:
            self.after(0, self._copy_done, dest, 0, 0, [])
            return
        success = 0
        failed = 0
        failed_files = []
        for i, src in enumerate(files_to_copy, 1):
            try:
                if progress_cb:
                    progress_cb(i, total, src.name)
                r = _rating_of(src)
                if rating_mode in ("folder", "both") and r >= rating_thr:
                    dst = copy_file(src, rated_dir)
                else:
                    dst = copy_file(src, dest)
                if verify:
                    try:
                        from .file_scanner import compute_file_hash
                        if compute_file_hash(src) != compute_file_hash(dst):
                            try:
                                dst.unlink(missing_ok=True)
                            except Exception:
                                pass
                            raise OSError("hash mismatch")
                    except OSError:
                        raise
                    except Exception as ve:
                        raise OSError(f"verify failed: {ve}")
                if rating_mode in ("metadata", "both") and r > 0:
                    try:
                        write_rating(dst, r)
                    except Exception:
                        pass
                success += 1
            except Exception as e:
                failed += 1
                failed_files.append((src, str(e)))
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
        self.rating_mode_var.set(saved.get("rating_mode", "metadata"))
        # New settings — dropdown, slider, toggle, thumb size
        cs = saved.get("copy_selection", "all")
        self.copy_selection_var.set("Selected only" if cs == "selected" else "All files")
        thr = int(saved.get("rating_threshold", 1))
        thr = max(1, min(5, thr))
        self.rating_threshold_var.set(thr)
        try:
            self.rating_threshold_slider.set(thr)
            self.rating_threshold_label.configure(text=f"≥{thr} ★")
        except Exception:
            pass
        self.verify_var.set(bool(saved.get("verify_after_copy", False)))
        ts = saved.get("thumb_size", "auto")
        disp = {"auto": "Auto", "S": "Small", "M": "Medium", "L": "Large",
                "small": "Small", "medium": "Medium", "large": "Large"}.get(str(ts), "Auto")
        self.thumb_size_var.set(disp)
        # Apply thumb size immediately for next scan
        size_map = {"S": (90, 68), "M": (120, 90), "L": (150, 113)}
        key = str(ts).upper() if isinstance(ts, str) else ""
        if key in size_map:
            import sd_mover.gui as _g
            _g.THUMB_SIZE = size_map[key]

        # Apply theme
        theme = saved.get("theme", "system")
        self.theme_var.set(theme)
        self._set_theme(theme)
