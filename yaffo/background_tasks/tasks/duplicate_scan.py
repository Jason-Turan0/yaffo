"""System automation `duplicate_scan`: on a schedule, scan selected indexed media for
perceptual-hash duplicates. The handler enqueues `duplicate_scan_task`, which
creates a `find_duplicates` Job (tagged with `automation_id` as the run, like
file_sync) and hands it to `find_duplicates_task` -- the exact scan the manual
Remove Duplicates tool runs, so its results surface in the UI identically. Mirrors
the file_sync built-in: a lightweight handler enqueuing a task that does the work.
"""
import json
import uuid

from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.automation_runs import RunOutcome, record_run
from yaffo.utils import job_codes as codes
from yaffo.background_tasks.events import EventContext
from yaffo.background_tasks.registry import register_handler
from yaffo.background_tasks.tasks.find_duplicates import find_duplicates_task
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.repositories.job_repository import run_job_id
from yaffo.db.models import Automation, Job, JOB_STATUS_PENDING, AUTOMATION_HANDLER_DUPLICATE_SCAN
from yaffo.db.repositories import media_repository
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')


def _open_scan_job(session, automation_id: int | None, media_item_ids: list[int] | None = None,
                   job_id: str | None = None) -> tuple[str, list[str]] | None:
    """Create a find_duplicates Job over selected indexed media items, tagged with
    `automation_id` as the run. Returns None when there is nothing to scan;
    the caller records that empty automation run through `record_run`. `job_id` is
    the task's queue id (a fresh id when None)."""
    job_id = job_id or str(uuid.uuid4())
    file_paths = (media_repository.get_all_media_item_paths(session) if media_item_ids is None
                  else list(media_repository.get_paths_by_ids(session, media_item_ids).values()))
    if not file_paths:
        return None
    session.add(Job(
        id=job_id,
        name='find_duplicates',
        status=JOB_STATUS_PENDING,
        task_count=len(file_paths),
        message='Processed {totalCount}/{taskCount} media items',
        completed_count=0,
        error_count=0,
        cancelled_count=0,
        automation_id=automation_id,
        job_data=json.dumps({'total_files': len(file_paths)}),
    ))
    session.commit()
    return job_id, file_paths


@task_queue.task(context=True)
def duplicate_scan_task(automation_id: int | None = None, media_item_ids: list[int] | None = None, task=None):
    """Open a find_duplicates Job over every indexed media item and enqueue the scan.
    `automation_id` tags the Job as that automation's run. An empty automation
    scan is recorded through the shared run handler."""
    job_id = run_job_id(task)
    session = SessionFactory()
    try:
        if session.get(Job, job_id) is not None:
            # A queue retry of a scan that already opened its Job: that attempt
            # queued the hashing (or recorded the empty run), so there's nothing to redo.
            logger.info(f"duplicate_scan: run {job_id} already opened; not scanning again")
            return
        opened = _open_scan_job(session, automation_id, media_item_ids, job_id)
        if opened is None and automation_id is not None:
            automation = session.get(Automation, automation_id)
            if automation is not None:
                record_run(session, automation, lambda _: RunOutcome(codes.OUTCOME_NO_MEDIA),
                           media_item_ids=[], job_id=job_id)
    finally:
        session.close()
        SessionFactory.remove()

    if opened is None:
        logger.info("duplicate_scan: no indexed media to scan")
        return
    job_id, file_paths = opened
    find_duplicates_task(job_id=job_id, file_paths=file_paths)


@register_handler(AUTOMATION_HANDLER_DUPLICATE_SCAN)
def enqueue_duplicate_scan(automation: Automation, context: EventContext | None = None) -> None:
    """Handler for the built-in duplicate-scan automation: enqueue the task tagged
    with the automation's id and limited to the trigger's selected media IDs."""
    if context is None:
        duplicate_scan_task(automation.id)
    else:
        duplicate_scan_task(automation.id, context.media_item_ids)
