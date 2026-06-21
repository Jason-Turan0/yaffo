from sqlalchemy.orm import joinedload
from yaffo.background_tasks.events import emit_event
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import PersonFace, FACE_STATUS_ASSIGNED, Person, Face, EVENT_PHOTO_MODIFIED, Photo, \
    PHOTO_STATUS_INDEXED
from yaffo.db.repositories.person_repository import update_person_embedding
from yaffo.logging_config import get_logger
from yaffo.background_tasks.config import task_queue
from sqlalchemy.dialects.sqlite import insert
from yaffo.domain.compare_utils import calculate_similarity

logger = get_logger(__name__, 'background_tasks')


@task_queue.task()
def assign_faces_to_person(person_id: int, face_ids: list[int]):
    """Background task to assign faces to a person."""
    logger.debug(f"Starting assign_faces_to_person_task for person {person_id} with {len(face_ids)} faces")
    try:
        session = SessionFactory()

        person: Person | None = (
            session.query(Person)
                   .options(joinedload(Person.stage_embeddings))
                   .order_by(Person.name)
                   .get(int(person_id))
        )
        if not person:
            raise Exception(f"Person {person_id} not found")
        faces = (session.query(Face).filter(Face.id.in_(face_ids))).all()
        similarity_by_face_id = calculate_similarity(person, faces)
        session.query(PersonFace).filter(PersonFace.face_id.in_(face_ids)).delete(synchronize_session=False)
        stmt = insert(PersonFace).values([
            {"person_id": person_id, "face_id": fid, "similarity": similarity_by_face_id.get(int(fid))}
            for fid in face_ids
        ])
        session.execute(stmt)
        session.query(Face).filter(Face.id.in_(face_ids)).update({Face.status: FACE_STATUS_ASSIGNED},
                                                                 synchronize_session=False)
        # Set photo status back to INDEXED when faces are assigned
        photo_ids = [face.photo_id for face in faces]
        session.query(Photo).filter(Photo.id.in_(photo_ids)).update(
            {Photo.status: PHOTO_STATUS_INDEXED}, synchronize_session=False
        )
        session.commit()
        update_person_embedding(person_id, session)
        emit_event(EVENT_PHOTO_MODIFIED, {"photo_ids": list(set(photo_ids))})
        logger.debug(f"Finished assign_faces_to_person_task for person {person_id} with {len(faces)} faces")
    except Exception as e:
        logger.error("Failed to assign faces to person", exc_info=e)
