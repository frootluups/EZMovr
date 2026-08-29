"""Scan SD card for media files and handle deduplication."""

import hashlib
import os
from pathlib import Path
from typing import Callable, List, Optional, Tuple

SUPPORTED_EXTENSIONS = {
    # Images
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp",
    # RAW formats
    ".cr2", ".cr3",  # Canon
    ".arw", ".sr2",  # Sony
    ".nef",  # Nikon
    ".raf",  # Fuji
    ".orf",  # Olympus
    ".rw2",  # Panasonic
    ".dng",  # Adobe DNG / Leica
    ".pef",  # Pentax
    ".x3f",  # Sigma
    # Video
    ".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts", ".3gp",
}


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tiff", ".tif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts", ".3gp"}
RAW_EXTENSIONS = {".cr2", ".cr3", ".arw", ".sr2", ".nef", ".raf", ".orf", ".rw2", ".dng", ".pef", ".x3f"}


def is_media_file(path: Path) -> bool:
    """Check if a file has a supported media extension."""
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def is_image(path: Path) -> bool:
    """Check if a file is a viewable image (not RAW or video)."""
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _walk_media(root: str, progress_cb: Optional[Callable[[int], None]], throttle: int) -> List[str]:
    """Iterative os.scandir walk returning paths of supported media files.

    Much faster than Path.rglob: avoids per-entry Path object construction
    and only stats files, with follow_symlinks disabled for speed/safety.
    """
    files = []
    stack = [root]
    count = 0
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            ext = os.path.splitext(entry.name)[1].lower()
                            if ext in SUPPORTED_EXTENSIONS:
                                files.append(entry.path)
                                count += 1
                                if progress_cb and count % throttle == 0:
                                    progress_cb(count)
                    except OSError:
                        continue
        except OSError:
            continue
    return files


def scan_drive(
    drive_path: str,
    progress_cb: Optional[Callable[[int], None]] = None,
    throttle: int = 200,
) -> List[Path]:
    """Recursively scan a drive for all supported media files.

    Returns a sorted list of file paths. progress_cb is called periodically
    with the running file count so the UI can stay responsive on long scans.
    """
    root = str(drive_path)
    if not os.path.isdir(root):
        return []

    raw = _walk_media(root, progress_cb, throttle)
    raw.sort(key=lambda p: os.path.basename(p).lower())
    return [Path(p) for p in raw]


def compute_file_hash(path: Path, chunk_size: int = 1 << 16) -> str:
    """Compute SHA-256 hash of a file using a 64KB buffer."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _index_destination(dest_dir: Path) -> dict:
    """Build {(file_name, file_size): [dest_path_str]} for media files.

    Uses a cheap file name + size key instead of hashing, so we only hash
    files that could actually be duplicates (identical name *and* size).
    """
    index = {}
    stack = [str(dest_dir)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            ext = os.path.splitext(entry.name)[1].lower()
                            if ext in SUPPORTED_EXTENSIONS:
                                size = entry.stat().st_size
                                index.setdefault((entry.name, size), []).append(entry.path)
                    except OSError:
                        continue
        except OSError:
            continue
    return index


def filter_new_files(
    source_files: List[Path], dest_dir: Path
) -> Tuple[List[Path], int]:
    """Filter source files to only those not already in dest_dir.

    Fast path: files whose (name, size) don't exist in the destination are
    new with no hashing. Only exact name+size collisions are hashed to fully
    verify duplication.

    Returns (new_files, duplicate_count).
    """
    dest = Path(dest_dir)
    if not dest.exists():
        return source_files, 0

    index = _index_destination(dest)
    if not index:
        return source_files, 0

    new_files = []
    dupes = 0
    for src in source_files:
        try:
            st = src.stat()
            candidates = index.get((src.name, st.st_size))
            if not candidates:
                new_files.append(src)
                continue

            src_hash = compute_file_hash(src)
            matched = False
            for cand in candidates:
                try:
                    if compute_file_hash(Path(cand)) == src_hash:
                        matched = True
                        break
                except OSError:
                    continue
            if matched:
                dupes += 1
            else:
                new_files.append(src)
        except OSError:
            new_files.append(src)

    return new_files, dupes


def get_files_summary(files: List[Path]) -> str:
    """Get a summary string of files to copy."""
    total_size = sum(f.stat().st_size for f in files if f.exists())
    size_mb = round(total_size / (1024 ** 2), 2)
    size_gb = round(total_size / (1024 ** 3), 2)

    by_ext = {}
    for f in files:
        ext = f.suffix.lower()
        by_ext[ext] = by_ext.get(ext, 0) + 1

    parts = [f"{len(files)} files"]
    if size_gb >= 1:
        parts.append(f"{size_gb} GB")
    else:
        parts.append(f"{size_mb} MB")

    ext_parts = [f"{count} {ext}" for ext, count in sorted(by_ext.items(), key=lambda x: -x[1])]
    return ", ".join(parts) + f"\nBreakdown: {', '.join(ext_parts)}"