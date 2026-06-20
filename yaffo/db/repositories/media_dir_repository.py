"""Media-dir-aware resolution for the data-query contract.

The data-query layer treats media directories and the on-disk folder tree as
first-class parts of the query language even though they aren't SQLAlchemy tables:

- `media_dir_id` / `relative_path` are *calculated* photo columns (derived from
  full_file_path, which is never exposed). This module maps a filter on them to
  concrete full_file_path SQL — so "photos in / under a folder", and counts of the
  same, fall out of the generic resolver.
- `media_dirs` and `folders` are *virtual sources*: their rows are computed from the
  media-dir registry + indexed paths, not selected from a table.

Kept in the db layer (no background_tasks dependency) so data_query_repository can
import it without inverting the layering.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import false, or_
from sqlalchemy.orm import Session

from yaffo.db.models import Photo, ApplicationSettings
from yaffo.db.repositories import photos_repository


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards in a literal so a path containing % or _ (or a crafted
    filter value) can't widen the match. Pairs with escape='\\\\' on .like()."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _under(root: Path):
    """Condition: full_file_path is a file anywhere under `root`."""
    return Photo.full_file_path.like(_escape_like(str(root) + os.sep) + "%", escape="\\")


def photo_path_conditions(session: Session, query: dict) -> list:
    """Translate a photos query's `media_dir_id` / `relative_path` filters into
    full_file_path SQL conditions (empty when neither is present). `relative_path`
    requires a single `media_dir_id` (`eq`); using it with `in` raises ValueError.
    An unknown media dir yields a never-match so the query returns nothing."""
    dir_filter = query.get("media_dir_id")
    rel_filter = query.get("relative_path")
    if not dir_filter and not rel_filter:
        return []

    entries = {m.id: m for m in get_media_dir_entries(session)}
    ids = [] if not dir_filter else (dir_filter["in"] if "in" in dir_filter else [dir_filter["eq"]])
    roots = [entries[i].path.resolve() for i in ids if i in entries]

    if rel_filter:
        if not dir_filter or "in" in dir_filter:
            raise ValueError("relative_path requires a single media_dir_id (use eq)")
        if not roots:
            return [false()]
        root = roots[0]
        if "eq" in rel_filter:
            return [Photo.full_file_path == str(root / rel_filter["eq"].replace("/", os.sep))]
        # prefix: every photo whose relative path starts with this string (recursive subtree)
        rel = rel_filter["prefix"].replace("/", os.sep).lstrip(os.sep)
        return [Photo.full_file_path.like(_escape_like(str(root) + os.sep + rel) + "%", escape="\\")]

    if not roots:
        return [false()]
    return [or_(*[_under(r) for r in roots])]

# The media_dirs setting stores a list of {"id": <uuid>, "path": <str>}. backfill
# (scripts/backfill_media_dir_ids.py) migrates any legacy string entries, and the
# add-media-dir route assigns a guid on insert, so readers can assume this schema.
def resolve_media_dirs(session: Session, query: dict) -> list[dict]:
    """Virtual `media_dirs` source: the configured dirs as {id, name}. The absolute
    path is never exposed (only the guid that photo rows carry + the folder name)."""
    return [{"id": m.id, "name": m.path.name} for m in get_media_dir_entries(session)]


def resolve_folders(session: Session, query: dict) -> list[dict]:
    """Virtual `folders` source: the immediate subfolders at (media_dir_id, path),
    each with a recursive count of photos indexed under it. Empty for an unknown
    media dir or a path that escapes it. Derived from full_file_path, never exposed."""
    media_dir = media_dir_by_id(session, query["media_dir_id"])
    if media_dir is None:
        return []
    root = media_dir.path.resolve()
    target = (root / query.get("path", "").replace("/", os.sep)).resolve()
    if target != root and root not in target.parents:
        return []
    # full_file_path is always a stored absolute path, so split it from target
    # lexically — no per-photo .resolve() (and its filesystem syscalls) needed.
    prefix = str(target) + os.sep
    counts: dict[str, int] = {}
    for _id, full in photos_repository.get_photo_paths_under_path(session, str(target)):
        if not full.startswith(prefix):
            continue
        subfolder, sep, _rest = full[len(prefix):].partition(os.sep)
        if sep:  # a separator after the subfolder name means the file is nested under it
            counts[subfolder] = counts.get(subfolder, 0) + 1
    return [{"name": name, "photo_count": counts[name]} for name in sorted(counts)]


@dataclass(frozen=True)
class MediaDir:
    """A configured media directory paired with a stable guid."""
    id: str
    path: Path


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
