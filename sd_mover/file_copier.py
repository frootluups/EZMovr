"""Copy files with progress tracking."""

import shutil
from pathlib import Path
from typing import Callable, List, Optional

try:
    import piexif  # noqa: F401  # ensure PyInstaller bundles it
except Exception:
    piexif = None  # type: ignore


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


def write_rating(file_path: Path, rating: int) -> bool:
    """Write a 0-5 star rating to file metadata or XMP sidecar.

    For JPEG/TIFF tries EXIF tags 0x4746 (Rating) + 0x4749 (RatingPercent).
    For other types (and as fallback) writes an XMP sidecar <stem>.xmp
    alongside the file. Returns True if sidecar or EXIF was written.
    """
    if not (1 <= rating <= 5):
        return False
    ext = file_path.suffix.lower()
    if ext in (".jpg", ".jpeg", ".tiff", ".tif"):
        try:
            import piexif

            exif_dict = piexif.load(str(file_path))
            # Windows Rating 1-5 and RatingPercent 1/25/50/75/99
            percent = {1: 1, 2: 25, 3: 50, 4: 75, 5: 99}.get(rating, rating * 20)
            exif_dict["0th"][0x4746] = rating
            exif_dict["0th"][0x4749] = percent
            exif_bytes = piexif.dump(exif_dict)
            piexif.insert(exif_bytes, str(file_path))
            return True
        except Exception:
            pass
    # Fallback / for non-JPEG: XMP sidecar <stem>.xmp (e.g. IMG_001.xmp)
    try:
        xmp_path = file_path.parent / f"{file_path.stem}.xmp"
        xmp = f'''<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="EZMovr">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/">
   <xmp:Rating>{rating}</xmp:Rating>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
'''
        xmp_path.write_text(xmp, encoding="utf-8")
        return True
    except Exception:
        return False
