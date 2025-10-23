import os
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from tqdm import tqdm

from photo_organizer.common import VIDEO_EXTENSIONS


def get_video_date(path: Path) -> Optional[datetime]:
    """
    Use exiftool to extract 'CreateDate' or 'DateTimeOriginal'.
    Fallback to file modified date if metadata missing.
    """
    try:
        result = subprocess.run(
            ["exiftool", "-CreateDate", "-DateTimeOriginal", "-s3", str(path)],
            capture_output=True,
            text=True
        )
        date_str = result.stdout.strip().splitlines()[0] if result.stdout else ""
        if date_str:
            for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
    except Exception:
        pass

    # fallback: file modified date
    try:
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts)
    except Exception:
        return None


def collect_video_files(src_dir: Path) -> list[Path]:
    """Recursively collect all video files in the source directory."""
    files = [
        f for f in src_dir.rglob("*")
        if f.is_file()
        and f.suffix.lower() in VIDEO_EXTENSIONS
        and not f.name.startswith("._")  # skip AppleDouble files
        and not f.name.startswith(".")   # skip hidden dotfiles
    ]
    return files


def organize_videos(files: List[Path], dest_dir: Path):
    """
    Move videos into dest_dir/year/month structure with progress bar.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    for file in tqdm(files, desc="Organizing videos", unit="file"):
        date_taken = get_video_date(file)

        if date_taken:
            year = date_taken.strftime("%Y")
            month = date_taken.strftime("%m")
            target_folder = dest_dir / year / month
        else:
            target_folder = dest_dir / file.suffix.lower().lstrip(".")

        target_folder.mkdir(parents=True, exist_ok=True)

        # avoid overwriting
        dest_path = target_folder / file.name
        counter = 1
        while dest_path.exists():
            dest_path = target_folder / f"{file.stem}_{counter}{file.suffix}"
            counter += 1

        shutil.move(str(file), str(dest_path))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Organize videos by taken date with progress")
    parser.add_argument("src_dir", type=Path, help="Directory containing videos")
    parser.add_argument("dest_dir", type=Path, help="Directory to move organized videos into")
    args = parser.parse_args()

    video_files = collect_video_files(args.src_dir)
    print(f"Found {len(video_files)} video files to organize.")
    organize_videos(video_files, args.dest_dir)
    print("✅ Done organizing videos!")
