import time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, joinedload

from yaffo.db.models import Job, JOB_STATUS_CANCELLED, Face, Person, JOB_STATUS_COMPLETED
from yaffo.common import DB_PATH
from yaffo.logging_config import get_logger

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={'check_same_thread': False},
    pool_pre_ping=True
)

SessionFactory = scoped_session(sessionmaker(bind=engine))
logger = get_logger(__name__, 'background_tasks')


def get_job_status(job_id: str) -> str:
    """Get the current status of a job."""
    session = SessionFactory()
    try:
        job = session.query(Job).filter_by(id=job_id).first()
        return job.status if job is not None else JOB_STATUS_CANCELLED
    finally:
        session.close()
        SessionFactory.remove()


def load_assign_faces_task_data(person_id: int, face_ids: list[int]) -> tuple[Person, list[Face]]:
    """Load person and faces data for face assignment tasks."""
    session = SessionFactory()
    try:
        person = session.query(Person).options(joinedload(Person.embeddings_by_year)).filter_by(id=person_id).first()
        faces = session.query(Face).filter(Face.id.in_(face_ids)).all()
        return person, faces
    finally:
        session.close()
        SessionFactory.remove()


def schedule_job_completion(job_id: str, delay_seconds: int = 2, max_wait_seconds: int = 30):
    """
    Schedule a job completion task.

    This should be called after enqueuing all other tasks for a job.
    The completion task will wait for all tasks to finish, then mark the job complete.

    Args:
        job_id: The job ID to schedule completion for
        delay_seconds: Delay before starting completion check (default: 2)
        max_wait_seconds: Maximum time to wait for completion (default: 30)

    Returns:
        The Huey Result object for the completion task
    """
    from yaffo.background_tasks.tasks.complete_job import complete_job_task
    return complete_job_task.schedule(
        args=(job_id, max_wait_seconds),
        delay=delay_seconds
    )