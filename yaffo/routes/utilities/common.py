from yaffo.db import db
from pathlib import Path

from yaffo.utils import settings


def is_system_file(filename: str) -> bool:
    system_files = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '.Spotlight-V100', '.Trashes', '.fseventsd'}
    return filename.startswith('._') or filename in system_files


def get_media_dirs() -> list[Path]:
    return settings.get_media_dirs(db.session)


def get_thumbnail_dir() -> Path | None:
    return settings.get_thumbnail_dir(db.session)