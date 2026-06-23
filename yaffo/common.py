import sys
from pathlib import Path
from platformdirs import user_cache_dir, user_data_dir
import os

app_author = "Jason Turan"
version = "0.0.1"
app_name = "yaffo"

# Media-type discriminator values. Defined here (a dependency-free leaf module) so
# both yaffo.common and yaffo.db.models can expose them without an import cycle;
# models re-exports these, so `from yaffo.db.models import MEDIA_TYPE_*` still works.
MEDIA_TYPE_PHOTO = "photo"
MEDIA_TYPE_VIDEO = "video"

# Pruned to the containers that play inline in the browser (H.264/HEVC in MP4/MOV)
# and that exiftool probes for metadata. Other containers (avi/mkv/wmv/flv) are
# left out for v1 — see docs/video.md Open Question #2.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic"}
MEDIA_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS


def media_type_for_path(path: Path) -> str:
    """The MEDIA_TYPE_* a file belongs to, from its suffix. Defaults to photo for
    anything that isn't a known video extension (the import path only ever sees
    files already filtered to MEDIA_EXTENSIONS)."""
    return MEDIA_TYPE_VIDEO if path.suffix.lower() in VIDEO_EXTENSIONS else MEDIA_TYPE_PHOTO

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
