"""Finish Jobs that nothing in the queue will finish any more.

A Job ends when its tasks say so: a chord's callback (finalize_job), a run task
finishing its own Job, or the host's failure hook. When that last write is lost
(the callback's commit hit a locked database, say) the Job stays PENDING or RUNNING
for ever, and the Library health page keeps its buttons disabled. Every minute this
sweep finds open Jobs that no unfinished queue work mentions and finishes them the
way their tasks would have. A host start is covered by its first tick.

Every Job is moved along by queue tasks that carry its id: chord members and
callbacks in their arguments, index Jobs waiting on their import chord in a
continuation, run Jobs as the task's own id (job_repository.run_job_id). So "no
ready or running task and no waiting chord mentions it" means nothing will.
"""
import time
from datetime import timedelta

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.tasks.complete_job import finalize_job
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import Job, JOB_STATUS_CANCELLED, JOB_STATUS_PENDING, JOB_STATUS_RUNNING
from yaffo.db.repositories.job_repository import fail_run_job
from yaffo.logging_config import get_logger
from yaffo.taskq import crontab
from yaffo.taskq.store import Store
from yaffo.utils.job_codes import PROBLEM_WORKER_STOPPED
from yaffo.utils.time import utcnow

logger = get_logger(__name__, 'background_tasks')

# A Job untouched for this long is a candidate. Covers the moments between a Job's
# commit and its tasks' enqueue (enqueue_index_jobs commits the Jobs first).
ORPHAN_GRACE = timedelta(minutes=5)
# The host adds a follow-up task right after marking the one before it done; a second
# look this much later sees it.
RECHECK_SECONDS = 2.0


def _unreferenced(store: Store, job_ids: set[str]) -> set[str]:
    task_ids, text = store.open_references()
    return {job_id for job_id in job_ids if job_id not in task_ids and job_id not in text}


def find_orphaned_jobs(session: Session, store: Store) -> list[str]:
    """Open Jobs (pending, running, or cancelled but never stopped) untouched for
    ORPHAN_GRACE that no unfinished queue work refers to, on two looks."""
    still_open = or_(Job.status.in_((JOB_STATUS_PENDING, JOB_STATUS_RUNNING)),
                     and_(Job.status == JOB_STATUS_CANCELLED, Job.completed_at.is_(None)))
    stale = func.coalesce(Job.updated_at, Job.created_at) < utcnow() - ORPHAN_GRACE
    candidates = {job_id for (job_id,) in session.query(Job.id).filter(still_open, stale)}
    if not candidates:
        return []
    orphans = _unreferenced(store, candidates)
    if orphans:
        time.sleep(RECHECK_SECONDS)
        orphans &= _unreferenced(store, orphans)
    return sorted(orphans)


def finish_orphaned_job(store: Store, job_id: str) -> None:
    """A run Job's task ended without finishing it: failed, as the host's failure
    hook does. Any other Job: finalized as its chord callback would have, its
    unreported items counted as errors."""
    if store.has_task(job_id):
        session = SessionFactory()
        try:
            fail_run_job(session, job_id, "The run stopped without finishing", PROBLEM_WORKER_STOPPED)
        finally:
            session.close()
            SessionFactory.remove()
    else:
        finalize_job(job_id)


@task_queue.periodic_task(crontab(minute="*"))
def sweep_orphaned_jobs() -> None:
    store = task_queue.store
    session = SessionFactory()
    try:
        orphans = find_orphaned_jobs(session, store)
    finally:
        session.close()
        SessionFactory.remove()
    for job_id in orphans:
        logger.warning(f"Job {job_id} has no queue work left to finish it; finishing it now")
        try:
            finish_orphaned_job(store, job_id)
        except Exception:
            logger.exception(f"could not finish orphaned job {job_id}")
