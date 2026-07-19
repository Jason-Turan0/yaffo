from sqlalchemy.orm import Session, joinedload
from yaffo.background_tasks.events import emit_event
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import PersonFace, FACE_STATUS_ASSIGNED, Person, Face, EVENT_MEDIA_MODIFIED
from yaffo.db.repositories.person_repository import update_person_embedding
from yaffo.logging_config import get_logger
from yaffo.background_tasks.config import task_queue
from sqlalchemy.dialects.sqlite import insert

logger = get_logger(__name__, 'background_tasks')


def assign_faces_to_person_now(
    session: Session,
    person_id: int,
    face_ids: list[int],
    *,
    emit_change_event: bool = True,
) -> int:
    person: Person | None = (
        session.query(Person)
        .options(joinedload(Person.stage_embeddings))
        .get(int(person_id))
    )
    if person is None:
        raise ValueError(f"Person {person_id} not found")

    faces = session.query(Face).filter(Face.id.in_(face_ids)).all()
    resolved_face_ids = [face.id for face in faces]
    if not resolved_face_ids:
        return 0

    session.query(PersonFace).filter(PersonFace.face_id.in_(resolved_face_ids)).delete(
        synchronize_session=False
    )
    session.execute(
        insert(PersonFace).values(
            [
                {"person_id": person_id, "face_id": face_id, "similarity": None}
                for face_id in resolved_face_ids
            ]
        )
    )
    session.query(Face).filter(Face.id.in_(resolved_face_ids)).update(
        {Face.status: FACE_STATUS_ASSIGNED}, synchronize_session=False
    )
    media_item_ids = sorted({face.media_item_id for face in faces})
    session.commit()
    update_person_embedding(person_id, session)
    if emit_change_event:
        emit_event(EVENT_MEDIA_MODIFIED, {"media_item_ids": media_item_ids})
    return len(resolved_face_ids)


@task_queue.task()
def assign_faces_to_person(person_id: int, face_ids: list[int]):
    """Background task to assign faces to a person."""
    logger.debug(f"Starting assign_faces_to_person_task for person {person_id} with {len(face_ids)} faces")
    session = SessionFactory()
    try:

        assigned_count = assign_faces_to_person_now(session, person_id, face_ids)
        logger.debug(
            f"Finished assign_faces_to_person_task for person {person_id} "
            f"with {assigned_count} faces"
        )
    except Exception as e:
        logger.error("Failed to assign faces to person", exc_info=e)
    finally:
        session.close()
