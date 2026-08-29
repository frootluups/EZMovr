"""Onboarding / welcome screen for first-time users."""

import customtkinter as ctk
from tkinter import filedialog
from pathlib import Path

from . import settings

ACCENT = "#3B82F6"
ACCENT_HOVER = "#2563EB"

SUPPORTED_FORMATS = (
    "JPEG, PNG, TIFF, BMP, GIF, WebP\n"
    "RAW: CR2, CR3 (Canon) | ARW, SR2 (Sony) | NEF (Nikon)\n"
      "      RAF (Fuji) | ORF (Olympus) | RW2 (Panasonic)\n"
      "      DNG (Adobe/Leica) | PEF (Pentax) | X3F (Sigma)\n"
    "Video: MP4, MOV, AVI, MKV, MTS, M2TS, 3GP"
)


class OnboardingWindow(ctk.CTkToplevel):
    """A modal onboarding dialog shown on first launch."""

    def __init__(self, parent):
        super().__init__(parent)

        self.title("Welcome to EZMovr")
        self.geometry("560x560")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.transient(parent)
        self.grab_set()

        self.result = None

        self._build_ui()

    def _build_ui(self):
        # --- Icon / title area ---
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.pack(fill="x", pady=(30, 0))

        ctk.CTkLabel(
            title_frame,
            text="Welcome!",
            font=("", 30, "bold"),
        ).pack()

        ctk.CTkLabel(
            title_frame,
            text="Move photos & videos from your camera's SD card\nto organized folders on your PC.",
            font=("", 13),
            text_color="gray65",
            justify="center",
        ).pack(pady=(6, 0))

        # --- How it works ---
        info = ctk.CTkFrame(self)
        info.pack(fill="x", padx=30, pady=(20, 0))

        ctk.CTkLabel(info, text="How it works", font=("", 14, "bold")).pack(
            anchor="w", padx=16, pady=(12, 0),
        )

        steps = (
            "1.  Insert your camera's SD card\n"
            "2.  The app detects it automatically\n"
            "3.  Choose to copy all files or only new ones\n"
            "4.  Pick a destination folder\n"
            "5.  Review and approve  \u2014  done!"
        )
        ctk.CTkLabel(
            info, text=steps, font=("", 12), justify="left",
        ).pack(anchor="w", padx=16, pady=(6, 14))

        # --- Supported formats ---
        fmt = ctk.CTkFrame(self)
        fmt.pack(fill="x", padx=30, pady=(12, 0))

        ctk.CTkLabel(fmt, text="Supported formats", font=("", 14, "bold")).pack(
            anchor="w", padx=16, pady=(12, 0),
        )

        ctk.CTkLabel(
            fmt, text=SUPPORTED_FORMATS, font=("Consolas", 11),
            justify="left", text_color="gray60",
        ).pack(anchor="w", padx=16, pady=(6, 14))

        # --- Default folder ---
        folder = ctk.CTkFrame(self)
        folder.pack(fill="x", padx=30, pady=(12, 0))

        ctk.CTkLabel(folder, text="Default save location", font=("", 14, "bold")).pack(
            anchor="w", padx=16, pady=(12, 0),
        )

        ctk.CTkLabel(
            folder, text="Where should photos be saved by default?",
            font=("", 12), text_color="gray60",
        ).pack(anchor="w", padx=16, pady=(2, 6))

        row = ctk.CTkFrame(folder, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 14))

        self.folder_var = ctk.StringVar(value=str(Path.home() / "Pictures"))
        ctk.CTkEntry(row, textvariable=self.folder_var, width=340).pack(
            side="left", padx=(0, 8),
        )
        ctk.CTkButton(
            row, text="Browse", width=78, height=28,
            fg_color=("gray65", "#3a3a3a"),
            hover_color=("gray55", "#4a4a4a"),
            command=self._browse,
        ).pack(side="left")

        # --- Buttons ---
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=30, pady=(20, 24))

        ctk.CTkButton(
            btn_frame,
            text="Skip",
            width=90,
            height=38,
            fg_color="transparent",
            border_width=1,
            border_color=("gray60", "#555"),
            hover_color=("gray85", "#2a2a2a"),
            text_color=("gray30", "gray70"),
            command=self._on_skip,
        ).pack(side="left")

        ctk.CTkButton(
            btn_frame,
            text="OK",
            width=150,
            height=38,
            font=("", 14, "bold"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            command=self._on_start,
        ).pack(side="right")

    def _browse(self):
        folder = filedialog.askdirectory(title="Select default save folder")
        if folder:
            self.folder_var.set(folder)

    def _on_skip(self):
        """Skip onboarding with defaults."""
        self.result = {
            "onboarding_done": True,
            "base_folder": str(Path.home() / "Pictures"),
            "default_mode": "all",
            "default_dest_mode": "date",
        }
        self.grab_release()
        self.destroy()

    def _on_start(self):
        self.result = {
            "onboarding_done": True,
            "base_folder": self.folder_var.get(),
            "default_mode": "all",
            "default_dest_mode": "date",
        }
        self.grab_release()
        self.destroy()

    def _on_close(self):
        self._on_skip()
