"""Resolve ids to human labels for the test/preview action summaries: a photo's
file name, a person's name. Fall back to "photo N" / "person N" when missing."""
from sqlalchemy.orm import Session

from yaffo.db.repositories import person_repository, media_repository


def media_item_label(session: Session, media_item_id) -> str:
    return media_repository.get_media_item_filename(session, media_item_id) or f"photo {media_item_id}"


def person_label(session: Session, person_id) -> str:
    person = person_repository.get_person_by_id(session, person_id)
    return person.name if person is not None and person.name else f"person {person_id}"


def face_label(session: Session, face_id) -> str:
    filename = media_repository.get_media_item_filename_for_face(session, face_id)
    return f"a face in {filename}" if filename else f"face {face_id}"