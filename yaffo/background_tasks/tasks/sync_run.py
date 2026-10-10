"""Run a sync automation in a worker, recorded as a Job (the run history).

Enqueued by invoke_automation for automations with a `sync` column, under a lock
named for the automation, so one sync never runs twice at once (an overlapping run
is skipped; whatever it would have done is picked up by the run in progress or the
next one). The Job's id is the queue task id, so a crash retry records on the same
Job. The engine returns codes; the Job stores them and the run history translates
them (utils/job_codes.py).
"""
from sqlalchemy.orm import Session

from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import Automation, Job, MediaItem, JOB_STATUS_COMPLETED, JOB_STATUS_FAILED
from yaffo.db.repositories.job_repository import (
    is_job_cancelled, open_run_job, refresh_estimated_completion, run_job_id,
)
from yaffo.logging_config import get_logger
from yaffo.sync.engine import run_sync
from yaffo.sync.run_common import RunContext
from yaffo.utils import job_codes as codes
from yaffo.utils.index_jobs import enqueue_index_jobs, reindex_media_items
from yaffo.utils.time import utcnow

logger = get_logger(__name__, 'background_tasks')


def sync_lock_name(automation_id: int) -> str:
    return f"sync-{automation_id}"


def enqueue_sync_run(automation_id: int) -> None:
    sync_run_task.enqueue_locked(sync_lock_name(automation_id), automation_id)


def _index_files(session: Session, paths: list[str], automation_id: int) -> None:
    enqueue_index_jobs(session, paths, automation_id=automation_id)


def _reindex_items(session: Session, media_item_ids: list[int]) -> None:
    items = session.query(MediaItem).filter(MediaItem.id.in_(media_item_ids)).all()
    if items:
        reindex_media_items(session, items)


def _write_progress(session: Session, job_id: str, done: int, total: int) -> None:
    """Show a long run's progress on its Job. Called between batches, after the engine
    has committed the batch, so this commit carries only the counts."""
    session.query(Job).filter_by(id=job_id).update({"task_count": total, "completed_count": done})
    refresh_estimated_completion(session, job_id)
    session.commit()


def run_and_record_sync(session: Session, automation: Automation, job_id: str) -> Job | None:
    job = open_run_job(session, job_id, name=automation.slug, automation_id=automation.id,
                       message=automation.name)
    if job is None:
        logger.info(f"sync '{automation.slug}' run {job_id} already ended; not running it again")
        return None
    run = RunContext(job_id=job.id, index_files=_index_files, reindex_items=_reindex_items,
                     should_cancel=lambda: is_job_cancelled(session, job.id),
                     on_progress=lambda done, total: _write_progress(session, job.id, done, total))
    try:
        result = run_sync(session, automation.id, run)
    except Exception as e:
        session.rollback()
        logger.error(f"sync '{automation.slug}' run {job.id} failed: {e}", exc_info=True)
        job = session.get(Job, job.id)
        if not is_job_cancelled(session, job.id):
            job.status = JOB_STATUS_FAILED
        job.error = str(e)
        job.job_data = codes.with_problem(job.job_data, codes.PROBLEM_TASK_ERROR)
        job.completed_at = utcnow()
        session.commit()
        return job
    job = session.get(Job, job.id)
    if not is_job_cancelled(session, job.id):
        job.status = JOB_STATUS_FAILED if result.failed_run else JOB_STATUS_COMPLETED
    job.job_data = result.job_data()
    job.error = result.error
    done = sum(v for k, v in result.params.items() if k not in ("unchanged", "folder"))
    job.task_count = job.completed_count = done
    job.error_count = result.params.get("failed", 0) + result.params.get("unsupported", 0)
    job.completed_at = utcnow()
    session.commit()
    return job


@task_queue.task(context=True)
def sync_run_task(automation_id: int, task=None):
    session = SessionFactory()
    try:
        automation = session.get(Automation, automation_id)
        if automation is None or automation.sync is None:
            logger.warning(f"sync_run_task: sync automation {automation_id} not found")
            return
        run_and_record_sync(session, automation, run_job_id(task))
    finally:
        session.close()
        SessionFactory.remove()
