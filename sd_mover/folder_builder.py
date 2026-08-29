"""Build destination folders on the computer."""

from datetime import datetime
from pathlib import Path
from typing import Optional


def build_date_path(base_dir: str, prefix: str = "Camera") -> Path:
    """Build a date-based destination path.

    Returns e.g. C:/Users/You/Camera/2026/08/29/
    """
    now = datetime.now()
    return Path(base_dir) / prefix / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"


def build_named_path(base_dir: str, folder_name: str) -> Path:
    """Build a user-named destination path.

    Returns e.g. C:/Users/You/Vacation Photos/
    """
    clean_name = folder_name.strip().rstrip("/\\")
    return Path(base_dir) / clean_name


def ensure_folder(path: Path) -> Path:
    """Create the folder (and parents) if it doesn't exist.

    Returns the created path.
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def folder_exists(path: Path) -> bool:
    """Check if a folder already exists."""
    return path.is_dir()


def get_destination(
    mode: str,
    base_dir: str,
    folder_name: Optional[str] = None,
    prefix: str = "Camera",
) -> Path:
    """Build and return the full destination path based on mode.

    mode: "date" or "named"
    """
    if mode == "date":
        return build_date_path(base_dir, prefix)
    elif mode == "named":
        name = folder_name or "Camera Import"
        return build_named_path(base_dir, name)
    else:
        raise ValueError(f"Unknown destination mode: {mode}")
