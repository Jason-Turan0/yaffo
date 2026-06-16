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


def tag_photo(session: Session, photo_id: int, name: str, value: Any = None) -> None:
    photos_repository.add_tag(session, photo_id, name, value)


def summarize_tag_photo(args: list[Any], session: Session) -> str:
    photo_id = args[0] if args else None
    name = args[1] if len(args) > 1 else "?"
    value = args[2] if len(args) > 2 and args[2] else None
    label = f"{name}={value}" if value else name
    return f"Tag {photo_label(session, photo_id)} as '{label}'"


def rename_file(session: Session, photo_id: int, new_name: str) -> None:
    current = photos_repository.get_photo_path(session, photo_id)
    if not current:
        return
    new_path = Path(current).with_name(new_name)
    Path(current).rename(new_path)
    photos_repository.update_photo_path(session, photo_id, str(new_path))


def summarize_rename_file(args: list[Any], session: Session) -> str:
    photo_id = args[0] if args else None
    new_name = args[1] if len(args) > 1 else "?"
    return f"Rename {photo_label(session, photo_id)} to '{new_name}'"


def assign_face(session: Session, face_id: int, person_id: int) -> None:
    if person_repository.get_person_by_id(session, person_id) is None:
        return
    person_repository.link_face_to_person(session, person_id, face_id)


def summarize_assign_face(args: list[Any], session: Session) -> str:
    face_id = args[0] if args else None
    person_id = args[1] if len(args) > 1 else None
    return f"Assign {person_label(session, person_id)} to {face_label(session, face_id)}"