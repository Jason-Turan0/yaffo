import json
import numpy as np
from sqlalchemy.orm import joinedload, Session
import pydash as _
from yaffo.db.models import Person, Face, PersonEmbedding
from yaffo.domain.compare_utils import load_embedding
from yaffo.logging_config import get_logger

logger = get_logger(__name__)

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

        def get_year(face: Face):
            try:
                return int(face.photo.date_taken[:4])
            except (ValueError, AttributeError):
                return None

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