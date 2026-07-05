"""Mutating host capabilities an automation can perform (tag photos, rename their
files, move them, assign people to faces). The host API exposes these as **batch**
functions only -- each takes a list and persists the whole set in one transaction;
`move_media_item` / `rename_file` are internal per-item helpers, not host-exposed. Each
takes the run's session first, like the read-only host impls, and delegates DB work
to db/repositories. These are flagged `mutating` in HOST_API, so a test/preview
records the call but does NOT execute it (build_recording_host_functions) -- a test
never changes anything; only a real triggered run performs them.

Each capability ships with a `summarize_*(args, session)` that turns the call's
args into the friendly one-line action shown in the test UI (e.g. "Tag 3 photo(s)").
"""
from pathlib import Path
from typing import Annotated, Any, Optional

import send2trash
from sqlalchemy.orm import Session

from yaffo.background_tasks.events import emit_event
from yaffo.background_tasks.progress_reporter import ProgressReporter
from yaffo.db.models import EVENT_MEDIA_MODIFIED, Tag
from yaffo.db.repositories import person_repository, media_repository
from yaffo.db.repositories.media_dir_repository import media_dir_by_id
from yaffo.background_tasks.automation_sandbox.media_dirs import enrich_media_rows
from yaffo.db.repositories.data_query_repository import resolve_query
from yaffo.logging_config import get_logger

logger = get_logger(__name__, "background_tasks")


def _emit_media_modified(media_item_ids: list[int]) -> None:
    """Announce that a script changed these photos' exported data, so subscribers like
    export_photo_tag write the change to the file. Safe inside a run: emit_event stamps
    the run's causal chain (event_chain_scope), so the loop guard skips re-triggering
    the same automation. No-op for an empty set."""
    ids = list(dict.fromkeys(pid for pid in media_item_ids if pid is not None))  # distinct, ordered
    if ids:
        emit_event(EVENT_MEDIA_MODIFIED, {"media_item_ids": ids})

def data_query(
    session: Session, query: dict
) -> Annotated[Any, "A list of row dicts, or a single number/object for count/range queries."]:
    rows = resolve_query(session, query)
    if query.get("source") == "media_items" and isinstance(rows, list):
        return enrich_media_rows(session, rows)
    return rows

def summarize_data_query(args: list[Any], session: Session) -> str:
    query = args[0] if args and isinstance(args[0], dict) else {}
    return f"Looking up {query.get('source', 'data')}"


def report_progress(progress: Optional[ProgressReporter], completed: int, total: int) -> None:
    """Update the active run's progress (drives the run-history percentage and the
    "N of TOTAL processed" line). `progress` is the run's reporter, injected by the
    host; it's None in a test/preview (no Job), so the call is a harmless no-op."""
    if progress is not None:
        progress.progress_update(int(total), int(completed), 0, 0)


def summarize_report_progress(args: list[Any], session: Session) -> str:
    completed = args[0] if args else 0
    total = args[1] if len(args) > 1 else 0
    return f"Report progress: {completed}/{total}"


def _tag_value(value: Any) -> str | None:
    return str(value) if value else None


def _existing_tag_keys(session: Session, media_item_ids: list[int]) -> set[tuple[int, str, str | None]]:
    if not media_item_ids:
        return set()
    rows = (
        session.query(Tag.media_item_id, Tag.tag_name, Tag.tag_value)
        .filter(Tag.media_item_id.in_(media_item_ids))
        .all()
    )
    return {(media_item_id, name, value) for media_item_id, name, value in rows}


def tag_media_items(session: Session, tags: list[dict]) -> None:
    """Batch-add tags in one write, then announce the change (photo_modified) so
    export_photo_tag can write the tags into the files. `tags` is a list of
    {media_item_id, name, value?}."""
    items = list(dict.fromkeys(
        (tag["media_item_id"], tag["name"], _tag_value(tag.get("value")))
        for tag in tags
        if tag.get("media_item_id") is not None and tag.get("name")
    ))
    if not items:
        return
    existing = _existing_tag_keys(session, list(dict.fromkeys(media_item_id for media_item_id, _, _ in items)))
    items = [item for item in items if item not in existing]
    if not items:
        return
    media_repository.add_tags(session, items)
    _emit_media_modified([media_item_id for media_item_id, _, _ in items])


def summarize_tag_media_items(args: list[Any], session: Session) -> str:
    tags = args[0] if args and isinstance(args[0], list) else []
    return f"Tag {len(tags)} photo(s)"


def rename_files(session: Session, renames: list[dict]) -> None:
    """Batch-rename files in one transaction. `renames` is a list of
    {media_item_id, new_name}; each file is renamed in place, then all path updates commit
    once."""
    for entry in renames:
        media_item_id, new_name = entry.get("media_item_id"), entry.get("new_name")
        if media_item_id is not None and new_name:
            rename_file(session, media_item_id, new_name)
    session.commit()


def summarize_rename_files(args: list[Any], session: Session) -> str:
    renames = args[0] if args and isinstance(args[0], list) else []
    return f"Rename {len(renames)} file(s)"


def rename_file(session: Session, media_item_id: int, new_name: str) -> None:
    """Per-item helper for rename_files (not host-exposed; no commit -- the batch
    commits once)."""
    current = media_repository.get_media_item_path(session, media_item_id)
    if not current:
        return
    # basename only + in-place, so `new_name` can't escape the photo's folder
    new_path = Path(current).with_name(Path(new_name).name)
    Path(current).rename(new_path)
    media_repository.update_media_item_path(session, media_item_id, str(new_path))


def move_media_items(session: Session, moves: list[dict]) -> None:
    """Batch-move photos in one transaction. `moves` is a list of
    {media_item_id, media_dir_id, target_path}; each file is moved into its media dir
    (confined to it), then all path updates commit once."""
    for entry in moves:
        media_item_id = entry.get("media_item_id")
        media_dir_id = entry.get("media_dir_id")
        target_path = entry.get("target_path")
        if media_item_id is not None and media_dir_id is not None and target_path is not None:
            move_media_item(session, media_item_id, media_dir_id, target_path)
    session.commit()


def summarize_move_media_items(args: list[Any], session: Session) -> str:
    moves = args[0] if args and isinstance(args[0], list) else []
    return f"Move {len(moves)} photo(s)"


def move_media_item(session: Session, media_item_id: int, media_dir_id: str, target_path: str) -> None:
    """Per-item helper for move_media_items (not host-exposed; no commit -- the batch
    commits once)."""
    current = media_repository.get_media_item_path(session, media_item_id)
    media_dir = media_dir_by_id(session, media_dir_id)
    if not current or media_dir is None:
        return
    root = media_dir.path.resolve()
    destination = (root / target_path / Path(current).name).resolve()
    if destination == Path(current).resolve():
        return  # already where it'd land -- no-op (e.g. re-running an organize)
    try:
        destination.relative_to(root)  # refuse a target that escapes the media dir
    except ValueError:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    Path(current).rename(destination)
    media_repository.update_media_item_path(session, media_item_id, str(destination))


def assign_faces(session: Session, assignments: list[dict]) -> None:
    """Batch-assign faces to people in one write, then announce the change
    (photo_modified) for the faces' photos so export_photo_tag writes the new people
    into the files. `assignments` is a list of {face_id, person_id}; unknown people
    and already-assigned faces are skipped."""
    pairs = [
        (entry["person_id"], entry["face_id"])
        for entry in assignments
        if entry.get("person_id") is not None and entry.get("face_id") is not None
    ]
    known = person_repository.existing_person_ids(session, [person_id for person_id, _ in pairs])
    face_ids = [face_id for person_id, face_id in pairs if person_id in known]
    linked = person_repository.bulk_link_faces_to_people(
        session, [(person_id, face_id) for person_id, face_id in pairs if person_id in known]
    )
    if linked:
        _emit_media_modified(media_repository.get_media_item_ids_for_faces(session, face_ids))


def summarize_assign_faces(args: list[Any], session: Session) -> str:
    assignments = args[0] if args and isinstance(args[0], list) else []
    return f"Assign {len(assignments)} face(s)"


def delete_media_items(session: Session, media_item_ids: list[int]) -> None:
    """Delete photos: send each file to the OS trash (recoverable), then remove the
    photo and its faces/tags/labels from the index. `media_item_ids` is a list of ids.
    A photo whose file can't be trashed is left in the index (not half-deleted)."""
    if not media_item_ids:
        return
    paths = media_repository.get_paths_by_ids(session, media_item_ids)
    removed: list[int] = []
    for media_item_id in media_item_ids:
        path = paths.get(media_item_id)
        if not path or not Path(path).exists():
            removed.append(media_item_id)  # no live file -> just drop the index row
            continue
        try:
            send2trash.send2trash(str(Path(path)))
            removed.append(media_item_id)
        except Exception as e:
            logger.warning(f"delete_media_items: could not trash {path}: {e}")
    thumbnails = media_repository.delete_media_items(session, removed)
    for thumb in thumbnails:
        try:
            Path(thumb).unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"delete_media_items: could not remove face thumbnail {thumb}: {e}")


def summarize_delete_media_items(args: list[Any], session: Session) -> str:
    media_item_ids = args[0] if args and isinstance(args[0], list) else []
    return f"Delete {len(media_item_ids)} photo(s)"
