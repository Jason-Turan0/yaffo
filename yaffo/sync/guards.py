"""Folders a sync may not use, checked when the setup is saved and again at run
start: Yaffo must never import its own files or index its own exports.

- A Folder target can't be inside a library media folder or the thumbnail folder.
- A Folder source can't be inside a media folder or any sync's import root, and can't
  contain one (it would copy the library into itself).
- An import root must stay inside its media folder.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from sqlalchemy.orm import Session

from yaffo.db.models import SYNC_ROLE_SOURCE
from yaffo.db.repositories import sync_repository
from yaffo.db.repositories.media_dir_repository import get_media_dir_entries, media_dir_by_id
from yaffo.sync.files import is_within
from yaffo.sync.schemas import SyncSchemaError
from yaffo.utils.settings import get_thumbnail_dir

PROBLEM_INSIDE_LIBRARY = "inside_library"
PROBLEM_CONTAINS_LIBRARY = "contains_library"
PROBLEM_INSIDE_THUMBNAILS = "inside_thumbnails"
PROBLEM_INSIDE_IMPORT_ROOT = "inside_import_root"
PROBLEM_IMPORT_ROOT_OUTSIDE = "import_root_outside_media_dir"
PROBLEM_UNKNOWN_MEDIA_DIR = "unknown_media_dir"
PROBLEM_MEDIA_DIR_MISSING = "media_dir_missing"


def _overlaps(path: Path, other: Path) -> str | None:
    if is_within(path, other):
        return PROBLEM_INSIDE_LIBRARY
    if is_within(other, path):
        return PROBLEM_CONTAINS_LIBRARY
    return None


def resolve_import_root(session: Session, import_root: dict) -> tuple[Path | None, str | None]:
    """The absolute folder an import root names, or a problem code."""
    media_dir = media_dir_by_id(session, import_root["media_dir_id"])
    if media_dir is None:
        return None, PROBLEM_UNKNOWN_MEDIA_DIR
    if not media_dir.path.is_dir():
        return None, PROBLEM_MEDIA_DIR_MISSING
    parts = PurePosixPath(import_root.get("subpath") or "").parts
    if any(part in ("..", "") for part in parts):
        return None, PROBLEM_IMPORT_ROOT_OUTSIDE
    root = media_dir.path.joinpath(*parts)
    if not is_within(root, media_dir.path):
        return None, PROBLEM_IMPORT_ROOT_OUTSIDE
    return root, None


def import_roots(session: Session) -> list[Path]:
    roots = []
    for automation in sync_repository.sync_automations(session):
        try:
            config = sync_repository.load_sync(automation).config
        except SyncSchemaError:
            continue
        if config.role == SYNC_ROLE_SOURCE and config.import_root:
            root, _ = resolve_import_root(session, config.import_root)
            if root is not None:
                roots.append(root)
    return roots


def folder_target_problem(session: Session, folder: Path) -> str | None:
    for media_dir in get_media_dir_entries(session):
        if is_within(folder, media_dir.path):
            return PROBLEM_INSIDE_LIBRARY
    thumbnails = get_thumbnail_dir(session)
    if thumbnails is not None and is_within(folder, thumbnails):
        return PROBLEM_INSIDE_THUMBNAILS
    return None


def folder_source_problem(session: Session, folder: Path) -> str | None:
    for media_dir in get_media_dir_entries(session):
        problem = _overlaps(folder, media_dir.path)
        if problem:
            return problem
    for root in import_roots(session):
        if is_within(folder, root) or is_within(root, folder):
            return PROBLEM_INSIDE_IMPORT_ROOT
    return None
