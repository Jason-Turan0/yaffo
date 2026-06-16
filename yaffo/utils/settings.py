import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from yaffo.db.models import ApplicationSettings


@dataclass(frozen=True)
class MediaDir:
    """A configured media directory paired with a stable guid."""
    id: str
    path: Path


# The media_dirs setting stores a list of {"id": <uuid>, "path": <str>}. backfill
# (scripts/backfill_media_dir_ids.py) migrates any legacy string entries, and the
# add-media-dir route assigns a guid on insert, so readers can assume this schema.
def _read_media_dirs(session: Session) -> tuple[ApplicationSettings | None, list[dict]]:
    setting = session.query(ApplicationSettings).filter_by(name="media_dirs").first()
    raw = json.loads(setting.value) if setting and setting.value else []
    return setting, raw


def _write_media_dirs(session: Session, setting: ApplicationSettings | None, raw: list[dict]) -> None:
    if setting is None:
        session.add(ApplicationSettings(name="media_dirs", type="json", value=json.dumps(raw)))
    else:
        setting.value = json.dumps(raw)
    session.commit()


def list_media_dirs(session: Session) -> list[dict]:
    """The raw [{id, path}] entries, for the settings UI."""
    return _read_media_dirs(session)[1]


def get_media_dir_entries(session: Session) -> list[MediaDir]:
    return [MediaDir(id=e["id"], path=Path(e["path"])) for e in list_media_dirs(session)]


def get_media_dirs(session: Session) -> list[Path]:
    return [m.path for m in get_media_dir_entries(session)]


def media_dir_by_id(session: Session, media_dir_id: str) -> MediaDir | None:
    return next((m for m in get_media_dir_entries(session) if m.id == media_dir_id), None)


def add_media_dir(session: Session, path: str) -> MediaDir | None:
    """Append a media dir with a fresh guid; None if the path is already configured."""
    setting, raw = _read_media_dirs(session)
    if any(e["path"] == path for e in raw):
        return None
    entry = {"id": str(uuid.uuid4()), "path": path}
    raw.append(entry)
    _write_media_dirs(session, setting, raw)
    return MediaDir(id=entry["id"], path=Path(path))


def remove_media_dir(session: Session, index: int) -> str | None:
    """Remove the media dir at `index`; returns its path, or None if out of range."""
    setting, raw = _read_media_dirs(session)
    if index < 0 or index >= len(raw):
        return None
    removed = raw.pop(index)
    _write_media_dirs(session, setting, raw)
    return removed["path"]


def get_thumbnail_dir(session: Session) -> Path | None:
    setting = session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first()
    if setting and setting.value:
        return Path(setting.value)
    return None