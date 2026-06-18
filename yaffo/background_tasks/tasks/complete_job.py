import time

from yaffo.db.models import Job, JOB_STATUS_CANCELLED, JOB_STATUS_COMPLETED
from yaffo.logging_config import get_logger
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.events import emit_job_completed_event
from yaffo.background_tasks.utils import SessionFactory

logger = get_logger(__name__, 'background_tasks')


def finalize_job(job_id: str) -> None:
    """Mark a job COMPLETED and emit its completion event.

    The shared terminal step for the chord completion path. Idempotent and safe
    to call once a job's tasks have all finished: a CANCELLED job is left
    untouched and an already-COMPLETED job is a no-op. Chord-dispatched jobs
    reach here via complete_job_callback (no polling -- the chord only fires once
    every member finished); the legacy polling complete_job_task keeps its own
    logic for the duplicate-removal flow.
    """
    session = SessionFactory()
    try:
        job = session.query(Job).filter_by(id=job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found in finalize_job")
            return
        if job.status in (JOB_STATUS_CANCELLED, JOB_STATUS_COMPLETED):
            return
        job.status = JOB_STATUS_COMPLETED
        session.commit()
        logger.info(
            f"Job {job_id} completed: {job.completed_count} completed, "
            f"{job.error_count} errors, {job.cancelled_count} cancelled"
        )
        emit_job_completed_event(session, job)
    except Exception as e:
        logger.error(f"Error finalizing job {job_id}: {e}", exc_info=True)
        session.rollback()
    finally:
        session.close()
        SessionFactory.remove()


@task_queue.task()
def complete_job_callback(job_id: str, results=None) -> None:
    """Chord callback: fires once every member task of a job's chord has
    finished, so the Job's counts are already final -- just finalize. `job_id` is
    the bound argument; the task queue appends the member return values as `results`, which
    are unused (the callback is purely a completion barrier)."""
    finalize_job(job_id)


@task_queue.task()
def finalize_job_task(job_id: str) -> None:
    """Finalize a job with no member tasks to wait on (an empty import/index
    stage). Chord callbacks never fire for an empty group, so empty stages
    finalize through this task instead."""
    finalize_job(job_id)


@task_queue.task()
def complete_job_task(job_id: str, max_wait_seconds: int = 30):
    """
    Final task for a job that marks it as complete.

    Polls for completion every 1 second up to max_wait_seconds.
    After timeout, always marks job as complete regardless of state.

    Args:
        job_id: The job ID to complete
        max_wait_seconds: Maximum seconds to wait before forcing completion (default: 30)
    """
    logger.info(f"Starting complete_job_task for job {job_id} (max wait: {max_wait_seconds}s)")

    elapsed = 0

    while elapsed < max_wait_seconds:
        session = SessionFactory()
        try:
            job = session.query(Job).filter_by(id=job_id).first()
            if not job:
                logger.error(f"Job {job_id} not found in complete_job_task")
                return
            if job.status == JOB_STATUS_CANCELLED:
                return
            total_finished = job.completed_count + job.error_count + job.cancelled_count

            if total_finished >= job.task_count:
                job.status = JOB_STATUS_COMPLETED
                session.commit()
                logger.info(
                    f"Job {job_id} completed after {elapsed}s: "
                    f"{job.completed_count} completed, {job.error_count} errors, "
                    f"{job.cancelled_count} cancelled"
                )
                emit_job_completed_event(session, job)
                return
        except Exception as e:
            logger.error(f"Error checking job {job_id} completion: {e}", exc_info=True)
            session.rollback()
        finally:
            session.close()
            SessionFactory.remove()

        time.sleep(1)
        elapsed += 1

    session = SessionFactory()
    try:
        job = session.query(Job).filter_by(id=job_id).first()
        if job and job.status != JOB_STATUS_COMPLETED:
            total_finished = job.completed_count + job.error_count + job.cancelled_count
            job.status = JOB_STATUS_COMPLETED
            session.commit()
            logger.warning(
                f"Job {job_id} force-completed after {max_wait_seconds}s timeout. "
                f"Status: {total_finished}/{job.task_count} tasks finished"
            )
            emit_job_completed_event(session, job)
    except Exception as e:
        logger.error(f"Error force-completing job {job_id}: {e}", exc_info=True)
        session.rollback()
    finally:
        session.close()
        SessionFactory.remove()