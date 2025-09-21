import os
import re
import shutil
from pathlib import Path
from typing import Optional

from PIL import Image
from datetime import datetime
import piexif

from photo_organizer.common import PHOTO_EXTENSIONS


def get_date_from_filename(filename: str) -> Optional[datetime]:
    patterns = [
        r"(\d{4})(\d{2})(\d{2})",                # 20211205
        r"(\d{4})[-_](\d{2})[-_](\d{2})",        # 2021-12-05 / 2021_12_05
        r"(?:IMG|DSC|PXL|VID)[-_]?(\d{4})(\d{2})(\d{2})",  # IMG_20211205
        r"(\d{2})[-_](\d{2})[-_](\d{4})",        # 05-12-2021
    ]
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            try:
                groups = match.groups()
                if len(groups[0]) == 4:  # assume YYYY first
                    year, month, day = groups
                else:  # assume DD-MM-YYYY
                    day, month, year = groups
                return datetime(int(year), int(month), int(day))
            except Exception:
                continue
    return None

def get_photo_date(path: str) -> Optional[datetime]:
    """
    Extract photo date in this order:
    1. EXIF 'DateTimeOriginal'
    2. Filename pattern YYYYMMDD
    3. File modified date
    """
    # --- 1. Try EXIF metadata ---
    try:
        img = Image.open(path)
        exif_data = img.info.get("exif")
        if exif_data:
            exif_dict = piexif.load(exif_data)
            date_str = exif_dict["Exif"].get(piexif.ExifIFD.DateTimeOriginal)
            if date_str:
                return datetime.strptime(date_str.decode(), "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass

    # --- 2. Try filename ---
    date_from_filename = get_date_from_filename(path)
    if date_from_filename is not None:
        return date_from_filename

    # --- 3. Fallback: file modified date ---
    try:
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts)
    except Exception:
        return None

def organize_photos(input_dir: str, output_dir: str):
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    for file in input_path.rglob("*"):
        if file.is_file() and file.suffix.lower() in PHOTO_EXTENSIONS:
            date_taken = get_photo_date(str(file))

            if date_taken:
                year = str(date_taken.year)
                month = f"{date_taken.month:02d}"
                dest_dir = output_path / year / month
            else:
                dest_dir = output_path / "unknown"

            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / file.name

            # Avoid overwriting: add suffix if file exists
            counter = 1
            while dest_file.exists():
                dest_file = dest_dir / f"{file.stem}_{counter}{file.suffix}"
                counter += 1

            shutil.move(str(file), dest_file)
            print(f"Moved: {file} -> {dest_file}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Organize photos by EXIF date")
    parser.add_argument("input_dir", help="Directory with photos to organize")
    parser.add_argument("output_dir", help="Directory to move organized photos into")
    args = parser.parse_args()
    organize_photos(args.input_dir, args.output_dir)
