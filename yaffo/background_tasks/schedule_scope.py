"""Resolve a schedule's configured media roots and indexed subjects."""

from pathlib import Path

from sqlalchemy.orm import Session

from yaffo.db.repositories import media_dir_repository, media_repository


class ScheduleScopeError(ValueError):
    """A saved schedule scope no longer resolves inside configured media dirs."""


def selected_paths(session: Session, config: dict | None) -> list[Path]:
    """Return validated roots; an empty selection means every configured media dir.

    Resolve at dispatch time so removed media directories cannot leave a schedule
    operating on an old location. Folder paths must remain inside a configured root.
    """
    entries = media_dir_repository.get_media_dir_entries(session)
    roots = {entry.id: entry.path.resolve() for entry in entries}
    config = config or {}
    scope_type = config.get("scope_type")
    ids = config.get("media_dir_ids") or []
    folders = config.get("folder_paths") or []
    if scope_type == "everything" or (scope_type is None and not ids and not folders):
        return list(dict.fromkeys(roots.values()))
    if scope_type == "media_dirs":
        folders = []
    elif scope_type == "paths":
        ids = []
    if any(entry_id not in roots for entry_id in ids):
        raise ScheduleScopeError("A scheduled media directory is no longer configured")
    paths = [roots[entry_id] for entry_id in ids]
    for folder in folders:
        path = Path(folder).expanduser().resolve()
        if not any(path == root or root in path.parents for root in roots.values()):
            raise ScheduleScopeError(f"Scheduled folder is outside configured media directories: {path}")
        paths.append(path)
    return list(dict.fromkeys(paths))


def media_item_ids(session: Session, paths: list[Path]) -> list[int]:
    return sorted({item_id for path in paths for item_id in media_repository.get_media_item_ids_under_path(session, str(path))})
