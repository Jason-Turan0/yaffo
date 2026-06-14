import json
from pathlib import Path

from sqlalchemy.orm import Session

from yaffo.db.models import ApplicationSettings


def get_media_dirs(session: Session) -> list[Path]:
    setting = session.query(ApplicationSettings).filter_by(name="media_dirs").first()
    if setting and setting.value:
        return [Path(dir_path) for dir_path in json.loads(setting.value)]
    return []


def get_thumbnail_dir(session: Session) -> Path | None:
    setting = session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first()
    if setting and setting.value:
        return Path(setting.value)
    return None