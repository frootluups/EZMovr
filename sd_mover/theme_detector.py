"""Detect and apply Windows system theme."""

import ctypes
import winreg
from typing import Literal

ThemeChoice = Literal["system", "light", "dark"]


def get_windows_theme() -> str:
    """Read the current Windows app theme from the registry.

    Returns "light" or "dark".
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return "light" if value == 1 else "dark"
    except (OSError, FileNotFoundError):
        return "dark"


def resolve_theme(choice: ThemeChoice) -> str:
    """Resolve a theme choice to the actual appearance mode.

    If choice is "system", read from Windows. Otherwise return as-is.
    """
    if choice == "system":
        return get_windows_theme()
    return choice


def apply_theme(choice: ThemeChoice) -> str:
    """Apply theme to customtkinter and return the resolved mode."""
    import customtkinter as ctk

    mode = resolve_theme(choice)
    ctk.set_appearance_mode(mode)
    return mode
