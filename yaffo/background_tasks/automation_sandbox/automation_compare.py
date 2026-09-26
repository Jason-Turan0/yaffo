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
from yaffo.domain.compare_utils import (
    calculate_face_similarity,
    calculate_similarity,
    load_embedding,
    ui_threshold_to_similarity,
)
from yaffo.domain.face_clusters import cluster_faces

# Unassigned faces read per call: the Faces page's batch size.
MAX_UNASSIGNED_FACES = 2000


def face_similarity(
    session: Session, media_item_id: int, person_id: int
) -> Annotated[list[dict], "A list of {face_id, score (0.0–1.0)}; empty if the person is unknown or the photo has no faces."]:
    """How similar each detected face in the photo is to the given person (0–1), as
    a list of {face_id, score}. Empty if the person is unknown or there are no faces."""
    person = person_repository.get_person_by_id(session, person_id)
    if person is None:
        return []
    faces = media_repository.get_faces_for_media_item(session, media_item_id, limit=5001)
    if len(faces) > 5000:
        raise ValueError("Face comparison row limit exceeded")
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
    people = person_repository.get_people_with_embeddings(session, limit=5001)
    name_by_id = {p.id: p.name for p in people}
    faces = media_repository.get_faces_for_media_item(session, media_item_id, limit=5001)
    if len(faces) * max(1, len(people)) > 5000:
        raise ValueError("Face comparison row limit exceeded")
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


def _min_similarity(session: Session, threshold: Any) -> float:
    """The Faces page's 0-100 similarity scale as a cosine similarity, calibrated to
    this library's band."""
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not 0 <= threshold <= 100:
        raise ValueError("threshold must be a number from 0 to 100")
    return ui_threshold_to_similarity(threshold, *person_repository.get_similarity_bounds(session))


def _bounded_limit(limit: Any, maximum: int) -> int:
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    return min(limit, maximum)


def suggest_face_clusters(
    session: Session, threshold: int = 50, limit: int = 20,
) -> Annotated[list[dict], "Clusters, largest first: {size, face_ids, media_item_ids}."]:
    """Group unassigned faces that look alike, as the Faces page does (Group by:
    Similarity), over the oldest 2,000 unassigned faces. `threshold` is the page's
    0-100 similarity scale: higher gives tighter, smaller clusters."""
    min_similarity = _min_similarity(session, threshold)
    limit = _bounded_limit(limit, 200)
    faces = person_repository.unassigned_faces(session, MAX_UNASSIGNED_FACES)
    photo_of_face = {face.id: face.media_item_id for face in faces}
    clusters = cluster_faces([face.id for face in faces], [load_embedding(face.embedding) for face in faces],
                             min_similarity)
    clusters.sort(key=lambda cluster: len(cluster[1]), reverse=True)
    return [
        {"size": len(face_ids), "face_ids": face_ids,
         "media_item_ids": list(dict.fromkeys(photo_of_face[face_id] for face_id in face_ids))}
        for _, face_ids in clusters[:limit]
    ]


def summarize_suggest_face_clusters(args: list[Any], session: Session) -> str:
    return "Group unassigned faces that look alike"


def find_similar_faces(
    session: Session, person_id: int, threshold: int = 50, limit: int = 200,
) -> Annotated[list[dict], "Unassigned faces, most similar first: {face_id, media_item_id, score (0.0–1.0)}."]:
    """Unassigned faces that look like a person, over the oldest 2,000 unassigned
    faces, scored against the person's faces from the same stage of life.
    `threshold` is the Faces page's 0-100 similarity scale. Empty when the person
    is unknown or has no assigned faces yet."""
    min_similarity = _min_similarity(session, threshold)
    limit = _bounded_limit(limit, MAX_UNASSIGNED_FACES)
    person = person_repository.get_person_by_id(session, person_id)
    if person is None:
        return []
    faces = person_repository.unassigned_faces(session, MAX_UNASSIGNED_FACES)
    photo_of_face = {face.id: face.media_item_id for face in faces}
    scores = [(face_id, score) for face_id, score in calculate_similarity(person, faces).items()
              if score >= min_similarity]
    scores.sort(key=lambda pair: pair[1], reverse=True)
    return [{"face_id": face_id, "media_item_id": photo_of_face[face_id], "score": round(score, 4)}
            for face_id, score in scores[:limit]]


def summarize_find_similar_faces(args: list[Any], session: Session) -> str:
    person_id = args[0] if args else None
    return f"Find unassigned faces that look like {person_label(session, person_id)}"
