import shutil
from pathlib import Path

from yaffo.common import PHOTO_EXTENSIONS
from yaffo.utils.photo_dates import get_photo_date


def organize_photos(input_dir: str, output_dir: str):
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    for file in input_path.rglob("*"):
        if file.is_file() and file.suffix.lower() in PHOTO_EXTENSIONS:
            date_taken = get_photo_date(str(file), None)

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
