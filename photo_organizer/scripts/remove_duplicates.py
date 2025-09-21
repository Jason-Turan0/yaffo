from pathlib import Path
from collections import defaultdict
from typing import List
from PIL import Image
import imagehash
from tqdm import tqdm
import shutil

from photo_organizer.common import PHOTO_EXTENSIONS, TRASH_DIR


def collect_photo_files(src_dir: Path) -> List[Path]:
    """Recursively collect all photo files."""
    return [
        f for f in src_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in PHOTO_EXTENSIONS
        and not f.name.startswith(".")  # skip hidden/system files
        and not f.name.startswith("._") # skip AppleDouble files
    ]


def hash_image(path: Path):
    """Return perceptual hash of an image."""
    try:
        return imagehash.phash(Image.open(path))
    except Exception:
        return None


def find_and_delete_duplicates(src_dir: Path):
    """Find visually similar photos and delete duplicates."""
    files = collect_photo_files(src_dir)
    print(f"Found {len(files)} photos to process.")

    TRASH_DIR.mkdir(exist_ok=True)
    hashes = defaultdict(list)

    for file in tqdm(files, desc="Hashing photos", unit="file"):
        h = hash_image(file)
        if h is not None:
            hashes[str(h)].append(file)

    # Only keep hash groups with more than one photo
    duplicates = {h: fs for h, fs in hashes.items() if len(fs) > 1}

    for hash_val, files in duplicates.items():
        # Keep the first file, delete/move the rest
        original = files[0]
        for dup in files[1:]:
            # Option 1: delete permanently
            # dup.unlink()

            # Option 2: move to trash
            shutil.move(str(dup), TRASH_DIR / dup.name)

            print(f"Duplicate removed: {dup} (kept {original})")

    print(f"✅ Done. {len(duplicates)} duplicate groups processed.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Find and delete visually similar photos")
    parser.add_argument("src_dir", type=Path, help="Directory containing photos")
    args = parser.parse_args()

    find_and_delete_duplicates(args.src_dir)
