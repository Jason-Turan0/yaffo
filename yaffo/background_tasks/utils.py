from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from yaffo.db.models import (
    Job, JOB_STATUS_CANCELLED,
    PageVersion, PAGE_VERSION_STATUS_CANCELLED, ApplicationSettings, Automation,
)
from yaffo.common import DB_PATH
from yaffo.logging_config import get_logger
from yaffo.themes import _setting_name, _theme_from_json
from yaffo.utils.settings import get_thumbnail_dir

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

def get_current_thumbnail_dir() -> Path | None:
    """Get the current status of a job."""
    session = SessionFactory()
    try:
       return get_thumbnail_dir(session)
    finally:
        session.close()
        SessionFactory.remove()


def get_version_status(version_id: int) -> str:
    """Current generation status of a page version -- the cancel signal the
    generate_page task polls between agent iterations. A missing version (deleted on
    cancel) reads as CANCELLED so the run stops."""
    session = SessionFactory()
    try:
        row = session.query(PageVersion.status).filter_by(id=version_id).first()
        return row[0] if row is not None else PAGE_VERSION_STATUS_CANCELLED
    finally:
        session.close()
        SessionFactory.remove()

def get_automation_status(slug: str) -> str | None:
    """Current generation status of an automation -- the cancel signal the
    generate_automation task polls between agent iterations. A missing automation
    (deleted mid-run) reads as None so the run stops."""
    session = SessionFactory()
    try:
        row = session.query(Automation.status).filter_by(slug=slug).first()
        return row[0] if row is not None else None
    finally:
        session.close()
        SessionFactory.remove()


def get_theme_status(slug: str) -> str:
    """Current generation status of a theme -- the cancel signal the
    generate_page task polls between agent iterations. A missing theme (deleted on
    cancel) reads as CANCELLED so the run stops."""
    session = SessionFactory()
    try:
        row = (
            session.query(ApplicationSettings)
            .filter_by(name=_setting_name(slug))
            .first()
        )
        if row is not None:
            return _theme_from_json(row.value).status
        else:
            return PAGE_VERSION_STATUS_CANCELLED
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
        The Result handle for the completion task
    """
    from yaffo.background_tasks.tasks.complete_job import complete_job_task
    return complete_job_task.schedule(
        args=(job_id, max_wait_seconds),
        delay=delay_seconds
    )