import time

from yaffo.db.models import Job, JOB_STATUS_CANCELLED, JOB_STATUS_COMPLETED
from yaffo.logging_config import get_logger
from yaffo.background_tasks.config import huey
from yaffo.background_tasks.utils import SessionFactory

logger = get_logger(__name__, 'background_tasks')


@huey.task()
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
    except Exception as e:
        logger.error(f"Error force-completing job {job_id}: {e}", exc_info=True)
        session.rollback()
    finally:
        session.close()
        SessionFactory.remove()