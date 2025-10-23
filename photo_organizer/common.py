from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".hevc"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic"}

from platformdirs import site_data_dir
import os

app_name = "PhotoOrganizer"
app_author = "Jason Turan"  # optional

data_dir = site_data_dir(app_name, app_author)
os.makedirs(data_dir, exist_ok=True)

ROOT_DIR = Path(data_dir)
TEMP_DIR = ROOT_DIR / "temp"
TRASH_DIR = ROOT_DIR / "duplicates"
MEDIA_DIRS = [
    ROOT_DIR / "organized"
]
THUMBNAIL_DIR = ROOT_DIR / "thumbnails"
DB_PATH = ROOT_DIR / "photos-organizer.db"
HUEY_DB_PATH = ROOT_DIR / "photo-organizer-huey.db"