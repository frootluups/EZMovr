"""CustomTkinter GUI for the SD Card Photo Mover."""

import threading
import customtkinter as ctk
from pathlib import Path
from tkinter import filedialog, messagebox

from PIL import Image

from .drive_detector import get_removable_drives, format_drive_info
from .file_scanner import scan_drive, filter_new_files, get_files_summary, is_image
from .folder_builder import get_destination, ensure_folder
from .file_copier import copy_files
from . import settings
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
    """Scrollable grid of photo thumbnails."""

    def __init__(self, master, **kwargs):
        super().__init__(master, label_text="Preview", **kwargs)
        self._thumbs = []  # prevent GC

    def clear(self):
        for w in self.winfo_children():
            w.destroy()
        self._thumbs.clear()

    def load_files(self, files):
        self.clear()
        if not files:
            ctk.CTkLabel(self, text="No previews available", text_color="gray50").pack(pady=30)
            return

        row_frame = None
        for i, f in enumerate(files):
            if i % COLS == 0:
                row_frame = ctk.CTkFrame(self, fg_color="transparent")
                row_frame.pack(fill="x", pady=THUMB_PAD)

            cell = ctk.CTkFrame(row_frame, fg_color="transparent")
            cell.pack(side="left", padx=THUMB_PAD)

            if is_image(f):
                self._load_thumbnail(cell, f)
            else:
                self._load_video_icon(cell, f)

    def _load_thumbnail(self, parent, path):
        try:
            img = Image.open(path)
            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)

            card = ctk.CTkFrame(parent, fg_color=("gray88", "#252525"), corner_radius=6)
            card.pack()

            lbl = ctk.CTkLabel(card, image=ctk_img, text="")
            lbl.image = ctk_img  # prevent GC
            lbl.pack(padx=4, pady=(4, 0))

            name = path.name if len(path.name) < 18 else path.name[:15] + "..."
            ctk.CTkLabel(card, text=name, font=("Consolas", 9), text_color="gray70").pack(
                padx=4, pady=(0, 4),
            )
            self._thumbs.append(ctk_img)
        except Exception:
            self._load_placeholder(parent, path, "Cannot preview")

    def _load_video_icon(self, parent, path):
        card = ctk.CTkFrame(parent, fg_color=("gray88", "#252525"), corner_radius=6)
        card.pack()

        ctk.CTkLabel(
            card, text="Video", font=("", 16, "bold"),
            width=THUMB_SIZE[0], height=THUMB_SIZE[1],
            fg_color=("gray75", "#333"), corner_radius=4,
        ).pack(padx=4, pady=(4, 0))

        name = path.name if len(path.name) < 18 else path.name[:15] + "..."
        ctk.CTkLabel(card, text=name, font=("Consolas", 9), text_color="gray70").pack(
            padx=4, pady=(0, 4),
        )

    def _load_placeholder(self, parent, path, label):
        card = ctk.CTkFrame(parent, fg_color=("gray88", "#252525"), corner_radius=6)
        card.pack()

        ctk.CTkLabel(
            card, text=label, font=("", 11),
            width=THUMB_SIZE[0], height=THUMB_SIZE[1],
            fg_color=("gray75", "#333"), corner_radius=4,
        ).pack(padx=4, pady=(4, 0))

        name = path.name if len(path.name) < 18 else path.name[:15] + "..."
        ctk.CTkLabel(card, text=name, font=("Consolas", 9), text_color="gray70").pack(
            padx=4, pady=(0, 4),
        )


class SDMoverApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("SD Card Photo Mover")
        self.geometry("1050x700")
        self.minsize(900, 600)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.drives = []
        self.selected_drive = None
        self.scanned_files = []
        self.copying = False

        self._build_ui()
        self._load_saved_settings()
        self._scan_drives()

        self.after(100, self._check_onboarding)

    def _build_ui(self):
        # --- Header ---
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(14, 6))

        ctk.CTkLabel(
            header, text="SD Card Photo Mover",
            font=("", 20, "bold"),
        ).pack(side="left")

        ctk.CTkLabel(
            header,
            text="Transfer photos and videos from your camera to your PC.",
            font=("", 11), text_color="gray55",
        ).pack(side="left", padx=14)

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

    def _scan_thread(self, drive_path):
        files = scan_drive(drive_path)

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

            self.deiconify()

    def _load_saved_settings(self):
        saved = settings.load_settings()
        self.base_folder_var.set(saved.get("base_folder", str(Path.home() / "Pictures")))
        self.mode_var.set(saved.get("default_mode", "all"))
        self.dest_mode_var.set(saved.get("default_dest_mode", "date"))
        self._toggle_dest_name()
