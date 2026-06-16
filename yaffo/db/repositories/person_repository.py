import json
import numpy as np
from sqlalchemy.orm import joinedload, Session
import pydash as _
from yaffo.db.models import Person, Face, PersonEmbedding, PersonFace
from yaffo.domain.compare_utils import load_embedding
from yaffo.logging_config import get_logger

logger = get_logger(__name__)


def get_or_create_person(session: Session, name: str) -> Person:
    person = session.query(Person).filter_by(name=name).first()
    if person is None:
        person = Person(name=name)
        session.add(person)
        session.commit()
    return person


def assign_person_to_photo_faces(session: Session, person_id: int, photo_id: int) -> int:
    """Link every face in the photo to the person (skipping faces already linked,
    since a face maps to one person). Returns how many new links were made."""
    linked = 0
    for face in session.query(Face).filter_by(photo_id=photo_id):
        if session.query(PersonFace).filter_by(face_id=face.id).first() is None:
            session.add(PersonFace(person_id=person_id, face_id=face.id))
            linked += 1
    session.commit()
    return linked

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
        person.avg_embedding = np.mean(embeddings, axis=0).tobytes()

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
            record.avg_embedding = avg_year.tobytes()
            record.included_face_ids = json.dumps(face_ids)

        session.commit()
    except Exception as e :
        logger.error(f"Failed to update person embedding for {person_id}", e)