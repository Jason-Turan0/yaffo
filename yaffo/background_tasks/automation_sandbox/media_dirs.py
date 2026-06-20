"""Media-dir guid plumbing for the automation host. Scripts work in
(media_dir_id, relative_path) rather than absolute paths: data_query photo rows are
enriched with those two fields (derived from full_file_path, which is never
exposed), and move_photo addresses a destination by media_dir_id.

The page-builder side of this (browsing the folder tree, listing media dirs) is now
served generically through the data-query contract — the `folders` / `media_dirs`
sources and the queryable media_dir_id / relative_path columns (see
db/repositories/media_dir_repository.py).
"""
from pathlib import Path

from sqlalchemy.orm import Session

from yaffo.db.repositories import photos_repository
from yaffo.db.repositories.media_dir_repository import MediaDir, get_media_dir_entries


def media_dir_for_path(entries: list[MediaDir], full_path: str) -> MediaDir | None:
    resolved = Path(full_path).resolve()
    for media_dir in entries:
        try:
            resolved.relative_to(media_dir.path.resolve())
            return media_dir
        except ValueError:
            continue
    return None


def enrich_photo_rows(session: Session, rows: list) -> list:
    """Add `media_dir_id` + `relative_path` to data_query photo rows, derived from
    each photo's full_file_path (kept server-side). Rows without an id (e.g. facet
    results) are left untouched."""
    ids = [r["id"] for r in rows if isinstance(r, dict) and "id" in r]
    if not ids:
        return rows
    paths = photos_repository.get_paths_by_ids(session, ids)
    entries = get_media_dir_entries(session)
    for row in rows:
        if not (isinstance(row, dict) and "id" in row):
            continue
        full = paths.get(row["id"])
        media_dir = media_dir_for_path(entries, full) if full else None
        row["media_dir_id"] = media_dir.id if media_dir else None
        row["relative_path"] = (
            str(Path(full).resolve().relative_to(media_dir.path.resolve()))
            if full and media_dir else None
        )
    return rows