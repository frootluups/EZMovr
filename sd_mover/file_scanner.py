"""Scan SD card for media files and handle deduplication."""

import hashlib
from pathlib import Path
from typing import List, Tuple

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


def scan_drive(drive_path: str) -> List[Path]:
    """Recursively scan a drive for all supported media files.

    Returns sorted list of file paths.
    """
    root = Path(drive_path)
    files = []
    if not root.exists():
        return files

    for f in root.rglob("*"):
        if f.is_file() and is_media_file(f):
            files.append(f)

    files.sort(key=lambda p: p.name)
    return files


def compute_file_hash(path: Path, chunk_size: int = 8192) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def build_destination_hashes(dest_dir: Path) -> dict:
    """Build a dict of {hash: Path} for all media files in destination directory."""
    hashes = {}
    if not dest_dir.exists():
        return hashes

    for f in dest_dir.rglob("*"):
        if f.is_file() and is_media_file(f):
            try:
                h = compute_file_hash(f)
                hashes[h] = f
            except (PermissionError, OSError):
                continue
    return hashes


def filter_new_files(
    source_files: List[Path], dest_dir: Path
) -> Tuple[List[Path], int]:
    """Filter source files to only those not already in dest_dir.

    Returns (new_files, duplicate_count).
    """
    existing_hashes = build_destination_hashes(dest_dir)
    if not existing_hashes:
        return source_files, 0

    new_files = []
    dupes = 0
    for src in source_files:
        try:
            src_hash = compute_file_hash(src)
            if src_hash not in existing_hashes:
                new_files.append(src)
            else:
                dupes += 1
        except (PermissionError, OSError):
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
