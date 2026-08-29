"""Auto-detect removable drives (SD cards) on Windows."""

import ctypes
import string
from pathlib import Path
from typing import List

import psutil


def get_removable_drives() -> List[dict]:
    """Return a list of removable drives with info.

    Returns list of dicts with keys: letter, label, total_gb, used_gb, free_gb.
    """
    drives = []
    for part in psutil.disk_partitions(all=False):
        if "removable" in part.opts.lower():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                total_gb = round(usage.total / (1024 ** 3), 2)
                free_gb = round(usage.free / (1024 ** 3), 2)
                used_gb = round(usage.used / (1024 ** 3), 2)
                drive_letter = part.mountpoint.replace("\\", "")
                drives.append({
                    "letter": drive_letter,
                    "label": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "opts": part.opts,
                    "total_gb": total_gb,
                    "used_gb": used_gb,
                    "free_gb": free_gb,
                })
            except PermissionError:
                continue
    return drives


def is_removable(drive_letter: str) -> bool:
    """Check if a drive letter is removable using Win32 API."""
    drive_path = f"{drive_letter}\\"
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(drive_path) == 2
    except Exception:
        return False


def get_removeable_drive_letters() -> List[str]:
    """Return just the drive letters of removable drives."""
    drives = get_removable_drives()
    return [d["letter"] for d in drives]


def format_drive_info(drive: dict) -> str:
    """Format drive info for display."""
    return (
        f"{drive['letter']}\\ - {drive['label']} "
        f"({drive['free_gb']}GB free / {drive['total_gb']}GB) [{drive['fstype']}]"
    )
