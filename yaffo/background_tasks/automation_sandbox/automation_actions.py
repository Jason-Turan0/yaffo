"""Mutating host capabilities an automation can perform (tag a photo, rename its
file, assign a person to a face). Each takes the run's session first, like the
read-only host impls, and delegates DB work to db/repositories. These are flagged
`mutating` in HOST_API, so a test/preview records the call but does NOT execute it
(build_recording_host_functions) -- a test never changes anything; only a real
triggered run performs them.

Each capability ships with a `summarize_*(args, session)` that turns the call's
args into the friendly one-line action shown in the test UI (e.g. "Tag
2024_beach.jpg as 'beach'"), resolving ids to names/file names via labels.
"""
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.labels import face_label, person_label, photo_label
from yaffo.db.repositories import person_repository, photos_repository
from yaffo.db.repositories.media_dir_repository import media_dir_by_id
from yaffo.background_tasks.automation_sandbox.media_dirs import enrich_photo_rows
from yaffo.db.repositories.data_query_repository import resolve_query

def data_query(session: Session, query: dict) -> Any:
    rows = resolve_query(session, query)
    if query.get("source") == "photos" and isinstance(rows, list):
        return enrich_photo_rows(session, rows)
    return rows

def summarize_data_query(args: list[Any], session: Session) -> str:
    query = args[0] if args and isinstance(args[0], dict) else {}
    return f"Looking up {query.get('source', 'data')}"

def tag_photos(session: Session, tags: list[dict]) -> None:
    """Batch-add tags in one write. `tags` is a list of {photo_id, name, value?}."""
    items = [
        (tag["photo_id"], tag["name"], tag.get("value"))
        for tag in tags
        if tag.get("photo_id") is not None and tag.get("name")
    ]
    photos_repository.add_tags(session, items)


def summarize_tag_photos(args: list[Any], session: Session) -> str:
    tags = args[0] if args and isinstance(args[0], list) else []
    return f"Tag {len(tags)} photo(s)"


def tag_photo(session: Session, photo_id: int, name: str, value: Any = None) -> None:
    photos_repository.add_tag(session, photo_id, name, value)


def summarize_tag_photo(args: list[Any], session: Session) -> str:
    photo_id = args[0] if args else None
    name = args[1] if len(args) > 1 else "?"
    value = args[2] if len(args) > 2 and args[2] else None
    label = f"{name}={value}" if value else name
    return f"Tag {photo_label(session, photo_id)} as '{label}'"


def rename_files(session: Session, renames: list[dict]) -> None:
    """Batch-rename files in one transaction. `renames` is a list of
    {photo_id, new_name}; each file is renamed in place, then all path updates commit
    once."""
    for entry in renames:
        photo_id, new_name = entry.get("photo_id"), entry.get("new_name")
        if photo_id is not None and new_name:
            rename_file(session, photo_id, new_name)
    session.commit()


def summarize_rename_files(args: list[Any], session: Session) -> str:
    renames = args[0] if args and isinstance(args[0], list) else []
    return f"Rename {len(renames)} file(s)"


def rename_file(session: Session, photo_id: int, new_name: str) -> None:
    current = photos_repository.get_photo_path(session, photo_id)
    if not current:
        return
    # basename only + in-place, so `new_name` can't escape the photo's folder
    new_path = Path(current).with_name(Path(new_name).name)
    Path(current).rename(new_path)
    photos_repository.update_photo_path(session, photo_id, str(new_path))


def summarize_rename_file(args: list[Any], session: Session) -> str:
    photo_id = args[0] if args else None
    new_name = args[1] if len(args) > 1 else "?"
    return f"Rename {photo_label(session, photo_id)} to '{Path(new_name).name}'"


def move_photos(session: Session, moves: list[dict]) -> None:
    """Batch-move photos in one transaction. `moves` is a list of
    {photo_id, media_dir_id, target_path}; each file is moved into its media dir
    (confined to it), then all path updates commit once."""
    for entry in moves:
        photo_id = entry.get("photo_id")
        media_dir_id = entry.get("media_dir_id")
        target_path = entry.get("target_path")
        if photo_id is not None and media_dir_id is not None and target_path is not None:
            move_photo(session, photo_id, media_dir_id, target_path)
    session.commit()


def summarize_move_photos(args: list[Any], session: Session) -> str:
    moves = args[0] if args and isinstance(args[0], list) else []
    return f"Move {len(moves)} photo(s)"


def move_photo(session: Session, photo_id: int, media_dir_id: str, target_path: str) -> None:
    current = photos_repository.get_photo_path(session, photo_id)
    media_dir = media_dir_by_id(session, media_dir_id)
    if not current or media_dir is None:
        return
    root = media_dir.path.resolve()
    destination = (root / target_path / Path(current).name).resolve()
    try:
        destination.relative_to(root)  # refuse a target that escapes the media dir
    except ValueError:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    Path(current).rename(destination)
    photos_repository.update_photo_path(session, photo_id, str(destination))


def summarize_move_photo(args: list[Any], session: Session) -> str:
    photo_id = args[0] if args else None
    media_dir_id = args[1] if len(args) > 1 else None
    target_path = args[2] if len(args) > 2 else "?"
    media_dir = media_dir_by_id(session, media_dir_id) if media_dir_id else None
    where = f"{target_path} in {media_dir.path.name}" if media_dir else target_path
    return f"Move {photo_label(session, photo_id)} to {where}"


def assign_faces(session: Session, assignments: list[dict]) -> None:
    """Batch-assign faces to people in one write. `assignments` is a list of
    {face_id, person_id}; unknown people and already-assigned faces are skipped."""
    pairs = [
        (entry["person_id"], entry["face_id"])
        for entry in assignments
        if entry.get("person_id") is not None and entry.get("face_id") is not None
    ]
    known = person_repository.existing_person_ids(session, [person_id for person_id, _ in pairs])
    person_repository.bulk_link_faces_to_people(
        session, [(person_id, face_id) for person_id, face_id in pairs if person_id in known]
    )


def summarize_assign_faces(args: list[Any], session: Session) -> str:
    assignments = args[0] if args and isinstance(args[0], list) else []
    return f"Assign {len(assignments)} face(s)"


def assign_face(session: Session, face_id: int, person_id: int) -> None:
    if person_repository.get_person_by_id(session, person_id) is None:
        return
    person_repository.link_face_to_person(session, person_id, face_id)


def summarize_assign_face(args: list[Any], session: Session) -> str:
    face_id = args[0] if args else None
    person_id = args[1] if len(args) > 1 else None
    return f"Assign {person_label(session, person_id)} to {face_label(session, face_id)}"