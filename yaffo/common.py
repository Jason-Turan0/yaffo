from pathlib import Path
from platformdirs import site_data_dir
import os

app_author = "Jason Turan"
version = "0.0.1"
app_name = "yaffo"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".hevc"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic"}

# Allow override via environment variable for testing
if os.environ.get("YAFFO_DATA_DIR"):
    data_dir = Path(os.environ["YAFFO_DATA_DIR"])
else:
    #data_dir = site_data_dir(app_name, app_author, ensure_exists=True)
    data_dir = Path("/Users/jason.turan/Pictures")

ROOT_DIR = Path(data_dir)
TEMP_DIR = ROOT_DIR / "temp"
TRASH_DIR = ROOT_DIR / "duplicates"
MEDIA_DIRS = [
    ROOT_DIR / "organized"
]
THUMBNAIL_DIR = ROOT_DIR / "thumbnails"
DB_PATH = ROOT_DIR / f"{app_name}.db"
HUEY_DB_PATH = ROOT_DIR / f"{app_name}-huey.db"