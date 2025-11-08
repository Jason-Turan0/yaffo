from yaffo.db import db
from yaffo.db.models import ApplicationSettings
from pathlib import Path
import json


def is_system_file(filename: str) -> bool:
    system_files = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '.Spotlight-V100', '.Trashes', '.fseventsd'}
    return filename.startswith('._') or filename in system_files


def get_media_dirs() -> list[Path]:
    media_dirs_setting = db.session.query(ApplicationSettings).filter_by(name="media_dirs").first()

    if media_dirs_setting and media_dirs_setting.value:
        media_dir_paths = json.loads(media_dirs_setting.value)
        return [Path(dir_path) for dir_path in media_dir_paths]
    else:
        return []


def get_thumbnail_dir() -> Path | None:
    thumbnail_setting = db.session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first()

    if thumbnail_setting and thumbnail_setting.value:
        return Path(thumbnail_setting.value)
    else:
        return None