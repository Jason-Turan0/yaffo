from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".hevc"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic"}

ROOT_DIR = Path("/Volumes/Jason-SDHC/Photos")
TEMP_DIR = ROOT_DIR / "temp"
TRASH_DIR = ROOT_DIR / "duplicates"
MEDIA_DIR = ROOT_DIR / "organized"
THUMBNAIL_DIR = ROOT_DIR / "thumbnails"
DB_PATH = ROOT_DIR / "photos-organizer.db"
HUEY_DB_PATH = ROOT_DIR / "photo-organizer-huey.db"