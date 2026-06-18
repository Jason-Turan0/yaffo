import json
import numpy as np
from sqlalchemy.orm import joinedload, Session
import pydash as _
from yaffo.db.models import Person, Face, PersonEmbedding, PersonFace
from yaffo.domain.compare_utils import load_embedding, serialize_embedding
from yaffo.logging_config import get_logger

logger = get_logger(__name__)


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
    """All people that have at least one per-year embedding, so face-similarity
    calculations have something to compare against (and don't max() over empty)."""
    return [p for p in session.query(Person).all() if p.embeddings_by_year]


def link_face_to_person(session: Session, person_id: int, face_id: int) -> bool:
    """Link one face to a person, unless it's already assigned (a face maps to one
    person). Returns whether a new link was made."""
    if session.query(PersonFace).filter_by(face_id=face_id).first() is not None:
        return False
    session.add(PersonFace(person_id=person_id, face_id=face_id))
    session.commit()
    return True

def update_person_embedding(person_id: int, session):
    try:
        person = (
            session.query(Person)
            .options(
                joinedload(Person.faces).joinedload(Face.photo),  # load photo for each face
                joinedload(Person.embeddings_by_year)  # load per-year embeddings
            )
            .filter(Person.id == person_id)
            .first()
        )
        if person is None:
            return

        # --- Compute overall avg_embedding ---
        embeddings = [load_embedding(f.embedding) for f in person.faces]
        person.avg_embedding = serialize_embedding(np.mean(embeddings, axis=0))

        def get_year(face: Face) -> int | None:
            return face.photo.year if face.photo else None

        faces_by_year = _.group_by(person.faces, get_year)

        # --- Compute per-year embeddings ---
        for year, faces_in_year in faces_by_year.items():
            if year is None:
                continue
            embs = [load_embedding(f.embedding) for f in faces_in_year]
            avg_year = np.mean(embs, axis=0)
            face_ids = [face.id for face in faces_in_year]

            # Fetch or create PersonEmbedding record
            record = session.query(PersonEmbedding).filter_by(person_id=person.id, year=year).first()
            if record is None:
                record = PersonEmbedding(person_id=person.id, year=year)
                session.add(record)
            record.avg_embedding = serialize_embedding(avg_year)
            record.included_face_ids = json.dumps(face_ids)

        session.commit()
    except Exception as e :
        logger.error(f"Failed to update person embedding for {person_id}", e)