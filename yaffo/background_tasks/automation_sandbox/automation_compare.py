"""Read-only face-similarity host capabilities for automations, wrapping the
embedding math in domain/compare_utils so a sandboxed script can reason about who
is in a photo (e.g. assign a person only when a face is similar enough).

These take script-friendly args (media_item_id / person_id), load the ORM entities via
db/repositories, and return scores as lists of string-keyed dicts. (Starlark itself
supports int-keyed dicts, but the starlark-pyo3 binding only converts a returned
Python dict when its keys are strings, so ids are values, not keys.) They're not
mutating, so a test/preview executes and records them like data_query.
"""
from typing import Annotated, Any

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.labels import person_label, media_item_label
from yaffo.db.repositories import person_repository, media_repository
from yaffo.domain.compare_utils import calculate_face_similarity, calculate_similarity


def face_similarity(
    session: Session, media_item_id: int, person_id: int
) -> Annotated[list[dict], "A list of {face_id, score (0.0–1.0)}; empty if the person is unknown or the photo has no faces."]:
    """How similar each detected face in the photo is to the given person (0–1), as
    a list of {face_id, score}. Empty if the person is unknown or there are no faces."""
    person = person_repository.get_person_by_id(session, person_id)
    if person is None:
        return []
    faces = media_repository.get_faces_for_media_item(session, media_item_id)
    scores = calculate_similarity(person, faces)
    return [{"face_id": face_id, "score": score} for face_id, score in scores.items()]


def summarize_face_similarity(args: list[Any], session: Session) -> str:
    media_item_id = args[0] if args else None
    person_id = args[1] if len(args) > 1 else None
    return f"Compare faces in {media_item_label(session, media_item_id)} to {person_label(session, person_id)}"


def match_people(
    session: Session, media_item_id: int
) -> Annotated[list[dict], "A list of {face_id, matches: [{person_id, person_name, score (0.0–1.0)}]}."]:
    """For each face in the photo, its similarity (0–1) to every known person, as a
    list of {face_id, matches: [{person_id, person_name, score}]}."""
    people = person_repository.get_people_with_embeddings(session)
    name_by_id = {p.id: p.name for p in people}
    faces = media_repository.get_faces_for_media_item(session, media_item_id)
    return [
        {
            "face_id": face.id,
            "matches": [
                {"person_id": pid, "person_name": name_by_id.get(pid), "score": score}
                for pid, score in calculate_face_similarity(face, people).items()
            ],
        }
        for face in faces
    ]


def summarize_match_people(args: list[Any], session: Session) -> str:
    media_item_id = args[0] if args else None
    return f"Match faces in {media_item_label(session, media_item_id)} to known people"