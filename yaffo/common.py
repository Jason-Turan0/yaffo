import sys
from pathlib import Path
from platformdirs import user_cache_dir, user_data_dir
import os

APP_AUTHOR = "Jason Turan"
APP_NAME = "yaffo"

# Media-type discriminator values. Defined here (a dependency-free leaf module) so
# both yaffo.common and yaffo.db.models can expose them without an import cycle;
# models re-exports these, so `from yaffo.db.models import MEDIA_TYPE_*` still works.
MEDIA_TYPE_PHOTO = "photo"
MEDIA_TYPE_VIDEO = "video"

# Containers that play inline in the browser's <video> (H.264/HEVC in MP4/MOV/M4V).
PLAYABLE_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
# All cataloged video. The non-playable containers (avi/mkv/wmv/flv) are still
# indexed for metadata + poster + faces (exiftool/ffmpeg handle them); the detail
# view offers "open externally" instead of an inline player. See docs/development/video.md.
VIDEO_EXTENSIONS = PLAYABLE_VIDEO_EXTENSIONS | {".avi", ".mkv", ".wmv", ".flv"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic"}
MEDIA_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS


def media_type_for_path(path: Path) -> str:
    """The MEDIA_TYPE_* a file belongs to, from its suffix. Defaults to photo for
    anything that isn't a known video extension (the import path only ever sees
    files already filtered to MEDIA_EXTENSIONS)."""
    return MEDIA_TYPE_VIDEO if path.suffix.lower() in VIDEO_EXTENSIONS else MEDIA_TYPE_PHOTO


def is_browser_playable_video(path: "Path | str") -> bool:
    """Whether a video plays inline in an HTML5 <video> (a container-level check).
    Non-playable containers are cataloged but opened in an external player."""
    return Path(path).suffix.lower() in PLAYABLE_VIDEO_EXTENSIONS

# Where the DB, thumbnails, temp/trash, and logs live. Set YAFFO_DATA_DIR to
# override (invoke sets it to ~/Pictures for dev). Otherwise default to the OS
# per-user data dir, so an installed app gets its own home (the photo library
# itself is configured in-app, independent of this).
if os.environ.get("YAFFO_DATA_DIR"):
    data_dir = Path(os.environ["YAFFO_DATA_DIR"])
else:
    data_dir = Path(user_data_dir(APP_NAME, APP_AUTHOR))

ROOT_DIR = Path(data_dir)
# Ensure the DB/log home exists before anything writes into it (logging opens a
# file here at import time, ahead of any migration step).
ROOT_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = ROOT_DIR / f"{APP_NAME}.db"
QUEUE_DB_PATH = ROOT_DIR / f"{APP_NAME}-queue.db"

# Downloaded vision models (CLIP ONNX encoders) cache here on first use, like
# InsightFace's ~/.insightface — kept out of the photo library / data dir.
MODEL_CACHE_DIR = Path(user_cache_dir(APP_NAME, APP_AUTHOR))

# Read-only assets shipped with the source tree and bundled into the app. When
# frozen by PyInstaller they live under sys._MEIPASS (the spec adds `resources`
# there); in dev `parents[1]` is the repo root, where `resources/` sits beside the
# `yaffo/` package. Models, when pre-bundled by the build script, live under
# BUNDLED_MODELS_DIR; loaders prefer them and fall back to a network download.
if getattr(sys, "frozen", False):
    BUNDLE_ROOT = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    RESOURCES_DIR = BUNDLE_ROOT / "resources"
else:
    BUNDLE_ROOT = Path(__file__).resolve().parents[1]
    _source_resources = BUNDLE_ROOT / "resources"
    _package_resources = Path(__file__).resolve().parent / "resources"
    RESOURCES_DIR = _source_resources if _source_resources.exists() else _package_resources
BUNDLED_MODELS_DIR = RESOURCES_DIR / "models"
