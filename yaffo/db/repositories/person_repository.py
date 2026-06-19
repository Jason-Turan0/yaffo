import json
from datetime import date

import numpy as np
from sqlalchemy.orm import joinedload, Session
import pydash as _
from yaffo.db.models import Person, Face, PersonEmbedding, PersonFace
from yaffo.domain.compare_utils import load_embedding, serialize_embedding
from yaffo.domain.life_stages import life_stage, effective_birthdate
from yaffo.logging_config import get_logger

logger = get_logger(__name__)


def _medoid(embeddings: list[np.ndarray]) -> np.ndarray:
    """The most representative real embedding of a group: the one closest (cosine)
    to the group centroid. A real face vector, not a blurry average -- so matching
    against it scores like a nearest-neighbour.

    Embeddings are unit-norm, so argmax over (emb · centroid) picks the same medoid
    whether or not the centroid is normalized -- and we deliberately do NOT
    normalize it: a diverse bucket's mean can be near-zero (e.g. near-orthogonal
    baby vs adult faces), and dividing by that tiny norm overflows float32."""
    # Drop any non-finite vectors (a degenerate face crop can yield NaN/inf), which
    # would otherwise poison the centroid and the argmax.
    finite = [e for e in embeddings if np.all(np.isfinite(e))]
    if len(finite) <= 1:
        return finite[0] if finite else embeddings[0]
    mat = np.vstack(finite)
    centroid = mat.mean(axis=0)
    # Dot to the centroid via an explicit reduction rather than `mat @ centroid`:
    # numpy's BLAS matmul (Apple Accelerate) emits spurious divide/overflow FPU
    # warnings here even though the inputs are unit vectors. Same result.
    sims = (mat * centroid).sum(axis=1)
    return finite[int(np.argmax(sims))]


def _estimate_birthdate(person: Person) -> date | None:
    """Median of (photo year − predicted age) over the person's faces. Single-face
    age is noisy, but the median over many photos is robust. Year precision."""
    births = [
        f.photo.year - f.estimated_age
        for f in person.faces
        if f.estimated_age is not None and f.photo is not None and f.photo.year is not None
    ]
    if not births:
        return None
    return date(int(round(float(np.median(births)))), 1, 1)


def get_person_by_id(session: Session, person_id: int) -> Person | None:
    return session.get(Person, person_id)


def get_photo_ids_for_person(session: Session, person_id: int) -> list[int]:
    """Distinct ids of photos that have at least one face linked to this person."""
    rows = (
        session.query(Face.photo_id)
        .join(PersonFace, PersonFace.face_id == Face.id)
        .filter(PersonFace.person_id == person_id, Face.photo_id.isnot(None))
        .distinct()
        .all()
    )
    return [photo_id for (photo_id,) in rows]


def get_people_with_embeddings(session: Session) -> list[Person]:
    """All people that have at least one per-stage embedding, so face-similarity
    calculations have something to compare against (and don't max() over empty)."""
    return [p for p in session.query(Person).all() if p.stage_embeddings]


def link_face_to_person(session: Session, person_id: int, face_id: int) -> bool:
    """Link one face to a person, unless it's already assigned (a face maps to one
    person). Returns whether a new link was made."""
    if session.query(PersonFace).filter_by(face_id=face_id).first() is not None:
        return False
    session.add(PersonFace(person_id=person_id, face_id=face_id))
    session.commit()
    return True

def update_person_embedding(person_id: int, session):
    """Recompute a person's estimated birthdate and their per-life-stage medoid
    gallery from their assigned faces. Stage embeddings are derived data, rebuilt
    from scratch each time. Bucketing uses the effective birthdate (actual if set,
    else estimated); with no birthdate every face lands in the 'unknown' stage."""
    try:
        person = (
            session.query(Person)
            .options(
                joinedload(Person.faces).joinedload(Face.photo),  # load photo for each face
                joinedload(Person.stage_embeddings),
            )
            .filter(Person.id == person_id)
            .first()
        )
        if person is None:
            return

        person.stage_embeddings.clear()  # delete-orphan removes old rows
        embeddings = [load_embedding(f.embedding) for f in person.faces]
        if not embeddings:
            person.avg_embedding = None
            person.estimated_birthdate = None
            session.commit()
            return

        person.avg_embedding = serialize_embedding(_medoid(embeddings))
        person.estimated_birthdate = _estimate_birthdate(person)
        birthdate = effective_birthdate(person)

        def stage_of(face: Face) -> str:
            photo_year = face.photo.year if face.photo else None
            return life_stage(birthdate, photo_year, face.estimated_age)

        session.flush()  # apply the clear() before inserting fresh rows (same PK)
        for stage, faces_in_stage in _.group_by(person.faces, stage_of).items():
            embs = [load_embedding(f.embedding) for f in faces_in_stage]
            person.stage_embeddings.append(PersonEmbedding(
                life_stage=stage,
                avg_embedding=serialize_embedding(_medoid(embs)),
                included_face_ids=json.dumps([f.id for f in faces_in_stage]),
            ))

        session.commit()
    except Exception as e:
        logger.error(f"Failed to update person embedding for {person_id}", e)