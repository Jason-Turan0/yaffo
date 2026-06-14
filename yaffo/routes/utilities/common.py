from yaffo.db import db
from pathlib import Path

from yaffo.utils import settings
from yaffo.utils.index_photos import is_system_file  # re-exported for route modules

__all__ = ["is_system_file", "get_media_dirs", "get_thumbnail_dir"]


def get_media_dirs() -> list[Path]:
    return settings.get_media_dirs(db.session)


def get_thumbnail_dir() -> Path | None:
    return settings.get_thumbnail_dir(db.session)