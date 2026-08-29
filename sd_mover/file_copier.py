"""Copy files with progress tracking."""

import shutil
from pathlib import Path
from typing import Callable, List, Optional


def copy_file(src: Path, dest_dir: Path) -> Path:
    """Copy a single file to destination directory.

    Returns the destination file path. Handles name collisions by appending _1, _2, etc.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name

    if dest.exists():
        stem = src.stem
        suffix = src.suffix
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    shutil.copy2(src, dest)
    return dest


def copy_files(
    files: List[Path],
    dest_dir: Path,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> tuple:
    """Copy multiple files with progress.

    Args:
        files: List of source file paths.
        dest_dir: Destination directory.
        progress_callback: Called with (current, total, filename).

    Returns:
        (success_count, failed_count, failed_files)
    """
    total = len(files)
    success = 0
    failed = 0
    failed_files = []

    for i, src in enumerate(files, 1):
        try:
            if progress_callback:
                progress_callback(i, total, src.name)
            copy_file(src, dest_dir)
            success += 1
        except (PermissionError, OSError) as e:
            failed += 1
            failed_files.append((src, str(e)))

    return success, failed, failed_files
