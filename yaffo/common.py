import sys
from pathlib import Path
from platformdirs import user_cache_dir, user_data_dir
import os

app_author = "Jason Turan"
version = "0.0.1"
app_name = "yaffo"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".hevc"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic"}

# Where the DB, thumbnails, temp/trash, and logs live. Set YAFFO_DATA_DIR to
# override (invoke sets it to ~/Pictures for dev). Otherwise default to the OS
# per-user data dir, so an installed app gets its own home (the photo library
# itself is configured in-app, independent of this).
if os.environ.get("YAFFO_DATA_DIR"):
    data_dir = Path(os.environ["YAFFO_DATA_DIR"])
else:
    data_dir = Path(user_data_dir(app_name, app_author))

ROOT_DIR = Path(data_dir)
# Ensure the DB/log home exists before anything writes into it (logging opens a
# file here at import time, ahead of any migration step).
ROOT_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = ROOT_DIR / f"{app_name}.db"
QUEUE_DB_PATH = ROOT_DIR / f"{app_name}-queue.db"

# Downloaded vision models (CLIP ONNX encoders) cache here on first use, like
# InsightFace's ~/.insightface — kept out of the photo library / data dir.
MODEL_CACHE_DIR = Path(user_cache_dir(app_name, app_author))

# Read-only assets shipped with the source tree and bundled into the app. When
# frozen by PyInstaller they live under sys._MEIPASS (the spec adds `resources`
# there); in dev `parents[1]` is the repo root, where `resources/` sits beside the
# `yaffo/` package. Models, when pre-bundled by the build script, live under
# BUNDLED_MODELS_DIR; loaders prefer them and fall back to a network download.
if getattr(sys, "frozen", False):
    BUNDLE_ROOT = Path(sys._MEIPASS)  # type: ignore[attr-defined]
else:
    BUNDLE_ROOT = Path(__file__).resolve().parents[1]
RESOURCES_DIR = BUNDLE_ROOT / "resources"
BUNDLED_MODELS_DIR = RESOURCES_DIR / "models"
