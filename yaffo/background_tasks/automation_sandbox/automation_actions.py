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
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Optional

import send2trash
from sqlalchemy.orm import Session

from yaffo.background_tasks.events import emit_event
from yaffo.background_tasks.progress_reporter import ProgressReporter
from yaffo.db.models import EVENT_MEDIA_MODIFIED, ClassificationLabel, Tag
from yaffo.db.repositories import album_repository, classification_repository, person_repository, media_repository
from yaffo.db.repositories import sandbox_edit_repository as edits
from yaffo.db.repositories.media_dir_repository import media_dir_by_id
from yaffo.background_tasks.automation_sandbox.media_dirs import enrich_media_rows
from yaffo.db.repositories.data_query_repository import resolve_query, FIELDS_BY_SOURCE, validate_query
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
    errors = validate_query(query)
    if errors:
        raise ValueError("; ".join(errors))
    bounded = dict(query)
    if query.get("source") in FIELDS_BY_SOURCE and "op" not in query:
        bounded["limit"] = min(query.get("limit", 5000), 5000)
    rows = (resolve_query(session, bounded, row_limit=5000) if query.get("op") == "facet" or query.get("source") not in FIELDS_BY_SOURCE
            else resolve_query(session, bounded))
    if isinstance(rows, list):
        rows = rows[:5000]
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
    if new_path == Path(current):
        return
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
    links = [(person_id, face_id) for person_id, face_id in pairs if person_id in known]
    similarities = {entry["face_id"]: entry["similarity"] for entry in assignments if "similarity" in entry}
    linked = (person_repository.bulk_link_faces_to_people(session, links, similarities=similarities)
              if similarities else person_repository.bulk_link_faces_to_people(session, links))
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


# ---- albums ------------------------------------------------------------------
# Reading albums needs no host function: `albums` and `album_items` are data_query
# sources like any other table. These are the mutating half.
#
# create_album is IDEMPOTENT ON THE NAME, which the others are not: an automation
# runs again and again (nightly, or on every import), and the obvious script —
# "make sure album X exists, then put today's photos in it" — must not fail or
# multiply albums on its second run. Adding a photo that is already a member, or
# removing one that is not, is likewise a no-op rather than an error.

def create_album(
    session: Session, name: str, description: Optional[str] = None
) -> Annotated[int, "The album's id — new, or the existing album with that name."]:
    """Create an album, or return the existing one with that name. Idempotent, so a
    repeating automation can call it on every run."""
    existing = album_repository.get_album_by_name(session, (name or "").strip())
    if existing is not None:
        return existing.id
    return album_repository.create_album(session, name, description).id


def summarize_create_album(args: list[Any], session: Session) -> str:
    name = args[0] if args else ""
    return f"Create album '{name}'"


def update_album(
    session: Session, album_id: int, name: str, description: Optional[str] = None,
    expected: Optional[dict] = None,
) -> None:
    """Rename an album / change its description. Membership is not touched."""
    if expected is not None and edits.album_state(session, album_id) != expected:
        return
    album_repository.update_album(session, album_id, name, description)


def summarize_update_album(args: list[Any], session: Session) -> str:
    name = args[1] if len(args) > 1 else ""
    return f"Rename album to '{name}'"


def add_to_album(
    session: Session, album_id: int, media_item_ids: list[int], positions: Optional[list[int]] = None,
    restore_cover: Optional[int] = None,
) -> None:
    """Add photos to an album in one batched write. Photos already in it are skipped."""
    if positions is None:
        album_repository.add_items(session, album_id, list(media_item_ids or []))
    else:
        album_repository.add_items(session, album_id, list(media_item_ids or []), positions=positions)
    if restore_cover is not None:
        album_repository.restore_cover_if_empty(session, album_id, restore_cover)


def summarize_add_to_album(args: list[Any], session: Session) -> str:
    media_item_ids = args[1] if len(args) > 1 and isinstance(args[1], list) else []
    return f"Add {len(media_item_ids)} photo(s) to an album"


def remove_from_album(
    session: Session, album_id: int, media_item_ids: list[int], expected_positions: Optional[dict] = None,
) -> None:
    """Remove photos from an album in one batched write. The photos themselves are NOT
    deleted — only their membership."""
    if expected_positions is not None:
        current = edits.album_members(session, album_id, media_item_ids)
        media_item_ids = [item for item in media_item_ids if item in current
                          and expected_positions.get(str(item)) == current[item]]
    album_repository.remove_items(session, album_id, list(media_item_ids or []))


def summarize_remove_from_album(args: list[Any], session: Session) -> str:
    media_item_ids = args[1] if len(args) > 1 and isinstance(args[1], list) else []
    return f"Remove {len(media_item_ids)} photo(s) from an album"


def delete_album(session: Session, album_id: int, expected: Optional[dict] = None) -> None:
    """Delete an album and its membership rows. The photos themselves are NOT deleted,
    and neither are their files."""
    if expected is not None and not edits.album_is_unchanged(session, album_id, expected):
        return
    album_repository.delete_album(session, album_id)


def summarize_delete_album(args: list[Any], session: Session) -> str:
    album_id = args[0] if args else ""
    album = album_repository.get_album(session, album_id) if isinstance(album_id, int) else None
    return f"Delete album '{album.name}'" if album else "Delete an album"


def set_album_cover(
    session: Session, album_id: int, media_item_id: Optional[int], expected: Optional[dict] = None,
) -> None:
    """Pin a member photo as the album's cover, or None to unpin it (the cover
    falls back to the first member). `expected` ({cover}) skips the change when the
    cover was changed since (undo after a later edit); an undo whose old cover has
    left the album unpins instead."""
    if expected is not None:
        if edits.album_cover(session, album_id) != expected.get("cover"):
            return
        if media_item_id is not None and not edits.album_members(session, album_id, [media_item_id]):
            media_item_id = None
    album_repository.set_cover(session, album_id, media_item_id)


def summarize_set_album_cover(args: list[Any], session: Session) -> str:
    return "Reset an album's cover" if len(args) > 1 and args[1] is None else "Set an album's cover"


def reorder_album(
    session: Session, album_id: int, media_item_ids: list[int], expected: Optional[dict] = None,
) -> None:
    """Put an album's photos in this order. Listed members come first, in the given
    order; members not listed follow in their current order. `expected` ({order})
    skips the change when the order was changed since (undo after a later edit)."""
    if album_repository.get_album(session, album_id) is None:
        raise ValueError(f"no album with id {album_id}")
    if expected is not None and edits.album_order(session, album_id) != expected.get("order"):
        return
    album_repository.reorder(session, album_id, edits.full_album_order(session, album_id, media_item_ids))


def summarize_reorder_album(args: list[Any], session: Session) -> str:
    return f"Reorder {len(args[1]) if len(args) > 1 else 0} photo(s) in an album"


# These batch forms accept an expected value for compare-and-restore undo.
def untag_media_items(session: Session, tags: list[dict]) -> None:
    _emit_media_modified(edits.remove_tags(session, tags))


def unassign_faces(session: Session, assignments: list[dict]) -> None:
    _emit_media_modified(edits.unassign_faces(session, assignments))


def set_coordinates(session: Session, values: list[dict]) -> None:
    """Set per-item GPS coordinates {id, latitude, longitude}, or null for both to
    clear them. Optional expected ([latitude, longitude]) skips later edits."""
    _emit_media_modified(edits.set_coordinates(session, values))


def ignore_faces(session: Session, face_ids: list[int]) -> None:
    """Mark unassigned faces as ignored (the Faces page's Ignore). Faces that are
    assigned, already ignored, or mid-assignment are left alone."""
    edits.set_face_status(session, face_ids, *edits.IGNORE)


def unignore_faces(session: Session, face_ids: list[int]) -> None:
    """Return ignored faces to Unassigned Faces. Other faces are left alone."""
    edits.set_face_status(session, face_ids, *edits.UNIGNORE)


def set_favorites(session: Session, values: list[dict]) -> None:
    _emit_media_modified(edits.set_values(session, values, "favorite"))


def set_media_dates(session: Session, values: list[dict]) -> None:
    _emit_media_modified(edits.set_values(session, values, "date"))


def set_location_names(session: Session, values: list[dict]) -> None:
    _emit_media_modified(edits.set_values(session, values, "location_name"))


def summarize_untag_media_items(args: list[Any], session: Session) -> str:
    return f"Remove {len(args[0])} tag(s)"


def summarize_unassign_faces(args: list[Any], session: Session) -> str:
    return f"Unassign {len(args[0])} face(s)"


def summarize_set_coordinates(args: list[Any], session: Session) -> str:
    return f"Set GPS coordinates for {len(args[0])} photo(s)"


def summarize_ignore_faces(args: list[Any], session: Session) -> str:
    return f"Ignore {len(args[0])} face(s)"


def summarize_unignore_faces(args: list[Any], session: Session) -> str:
    return f"Stop ignoring {len(args[0])} face(s)"


def summarize_set_favorites(args: list[Any], session: Session) -> str:
    return f"Set favorites for {len(args[0])} photo(s)"


def summarize_set_media_dates(args: list[Any], session: Session) -> str:
    return f"Set capture dates for {len(args[0])} photo(s)"


def summarize_set_location_names(args: list[Any], session: Session) -> str:
    return f"Set location names for {len(args[0])} photo(s)"


# ---- label vocabulary -------------------------------------------------------------
# The auto-classifier's vocabulary (Settings → Labels). Adding a label only takes
# effect for a photo when it's classified again (run_automation("classify_labels")).
# Like create_album, adding is idempotent on the name.

MAX_LABEL_NAME = 64
MAX_LABEL_PROMPT = 200


def add_label_to_vocabulary(
    session: Session, name: str, prompt: Optional[str] = None,
) -> Annotated[int, "The label's id — new, or the existing label with that name."]:
    """Add a label the classifier can give photos, or return the existing one with
    that name. `prompt` is the text it's matched by (default "a photo of <name>")."""
    name = (name or "").strip()
    if not name or len(name) > MAX_LABEL_NAME:
        raise ValueError(f"A label name must be 1 to {MAX_LABEL_NAME} characters")
    prompt = (prompt or "").strip() or None
    if prompt is not None and len(prompt) > MAX_LABEL_PROMPT:
        raise ValueError(f"A label prompt can be at most {MAX_LABEL_PROMPT} characters")
    existing = classification_repository.get_label_by_name(session, name)
    if existing is not None:
        return existing.id
    return classification_repository.create_label(session, name, prompt).id


def summarize_add_label_to_vocabulary(args: list[Any], session: Session) -> str:
    return f"Add label '{args[0] if args else ''}' to the classifier's vocabulary"


def delete_label(session: Session, label_id: int, expected: Optional[dict] = None) -> None:
    """Remove a label from the vocabulary; photos lose it. `expected` ({name,
    unused}) skips the delete when the label was used since (undo of an add)."""
    label = session.get(ClassificationLabel, label_id) if isinstance(label_id, int) else None
    if label is None:
        return
    if expected is not None:
        if label.name != expected.get("name"):
            return
        if expected.get("unused") and classification_repository.label_use_count(session, label_id):
            return
    classification_repository.delete_label(session, label_id)


def summarize_delete_label(args: list[Any], session: Session) -> str:
    label = session.get(ClassificationLabel, args[0]) if args and isinstance(args[0], int) else None
    return f"Delete label '{label.name}'" if label else "Delete a label"


# ---- people -------------------------------------------------------------------
# Reading people needs no host function (the `people` data_query source). Like
# create_album, create_person is IDEMPOTENT ON THE NAME, so a repeating automation
# can "make sure X exists" every run. Renaming, merging and deleting change the
# names written into photo files, so each announces the photos it touched.

def _person_name(session: Session, person_id: Any) -> str | None:
    person = person_repository.get_person_by_id(session, person_id) if isinstance(person_id, int) else None
    return person.name if person else None


def create_person(session: Session, name: str) -> Annotated[int, "The person's id — new, or the existing person with that name."]:
    """Create a person with no faces, or return the existing one with that name."""
    name = (name or "").strip()
    if not name:
        raise ValueError("A person's name can't be empty")
    existing = person_repository.get_person_by_name(session, name)
    if existing is not None:
        return existing.id
    return person_repository.create_person(session, name).id


def summarize_create_person(args: list[Any], session: Session) -> str:
    return f"Create person '{args[0] if args else ''}'"


def rename_person(session: Session, person_id: int, name: str, expected: Optional[str] = None) -> None:
    """Rename a person; the new name must not belong to someone else. `expected`
    skips the rename when the current name is no longer that (undo after a later
    edit)."""
    if expected is not None and _person_name(session, person_id) != expected:
        return
    person_repository.rename_person(session, person_id, name)
    _emit_media_modified(person_repository.get_media_item_ids_for_person(session, person_id))


def summarize_rename_person(args: list[Any], session: Session) -> str:
    old = _person_name(session, args[0]) if args else None
    new = args[1] if len(args) > 1 else ""
    return f"Rename person '{old}' to '{new}'" if old else f"Rename a person to '{new}'"


def _birthdate(value: Any) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("birthdate must be an ISO date string (YYYY-MM-DD) or None")
    return date.fromisoformat(value)


def person_birthdate(session: Session, person_id: Any) -> str | None:
    person = person_repository.get_person_by_id(session, person_id) if isinstance(person_id, int) else None
    return person.birthdate.isoformat() if person and person.birthdate else None


def set_person_birthdate(
    session: Session, person_id: int, birthdate: Optional[str], expected: Optional[dict] = None,
) -> None:
    """Set a person's birthdate (YYYY-MM-DD), or None to clear it. `expected`
    ({birthdate}) skips the change when it was changed since (undo after a later
    edit)."""
    if expected is not None and person_birthdate(session, person_id) != expected.get("birthdate"):
        return
    person_repository.set_birthdate(session, person_id, _birthdate(birthdate))


def summarize_set_person_birthdate(args: list[Any], session: Session) -> str:
    name = _person_name(session, args[0]) if args else None
    value = args[1] if len(args) > 1 else None
    return f"{'Set' if value else 'Clear'} the birthdate of '{name or 'a person'}'" + (f" to {value}" if value else "")


def merge_people(session: Session, source_person_id: int, target_person_id: int) -> None:
    """Move every face of the source person to the target and delete the source."""
    _emit_media_modified(person_repository.merge_people(session, source_person_id, target_person_id))


def summarize_merge_people(args: list[Any], session: Session) -> str:
    source = _person_name(session, args[0]) if args else None
    target = _person_name(session, args[1]) if len(args) > 1 else None
    return f"Merge '{source}' into '{target}'" if source and target else "Merge two people"


def delete_person(session: Session, person_id: int, expected: Optional[dict] = None) -> None:
    """Delete a person; their faces become unassigned. `expected` ({name, empty})
    skips the delete when the person was renamed or given faces since (undo of a
    create_person)."""
    if expected is not None:
        if _person_name(session, person_id) != expected.get("name"):
            return
        if expected.get("empty") and person_repository.count_person_faces(session, person_id):
            return
    _emit_media_modified(person_repository.delete_person(session, person_id))


def summarize_delete_person(args: list[Any], session: Session) -> str:
    name = _person_name(session, args[0]) if args else None
    return f"Delete person '{name}'" if name else "Delete a person"
