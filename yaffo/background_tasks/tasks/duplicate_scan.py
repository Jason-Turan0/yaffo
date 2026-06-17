"""System automation `duplicate_scan`: on a schedule, scan all indexed photos for
perceptual-hash duplicates. The handler enqueues `duplicate_scan_task`, which
creates a `find_duplicates` Job (tagged with `automation_id` as the run, like
file_sync) and hands it to `find_duplicates_task` -- the exact scan the manual
Remove Duplicates tool runs, so its results surface in the UI identically. Mirrors
the file_sync built-in: a lightweight handler enqueuing a task that does the work.
"""
import json
import uuid

from yaffo.background_tasks.config import huey
from yaffo.background_tasks.events import EventContext
from yaffo.background_tasks.registry import register_handler
from yaffo.background_tasks.tasks.find_duplicates import find_duplicates_task
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import Automation, Job, JOB_STATUS_PENDING, AUTOMATION_HANDLER_DUPLICATE_SCAN
from yaffo.db.repositories import photos_repository
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')


def _open_scan_job(session, automation_id: int | None) -> tuple[str, list[str]] | None:
    """Create a find_duplicates Job over every indexed photo, tagged with
    `automation_id` as the run. Returns (job_id, file_paths), or None when there's
    nothing to scan."""
    file_paths = photos_repository.get_all_photo_paths(session)
    if not file_paths:
        return None
    job_id = str(uuid.uuid4())
    session.add(Job(
        id=job_id,
        name='find_duplicates',
        status=JOB_STATUS_PENDING,
        task_count=len(file_paths),
        message='Processed {totalCount}/{taskCount} photos',
        completed_count=0,
        error_count=0,
        cancelled_count=0,
        automation_id=automation_id,
        job_data=json.dumps({'total_files': len(file_paths)}),
    ))
    session.commit()
    return job_id, file_paths


@huey.task()
def duplicate_scan_task(automation_id: int | None = None):
    """Open a find_duplicates Job over every indexed photo and enqueue the scan.
    `automation_id` tags the Job as that automation's run. No-op when there are no
    indexed photos."""
    session = SessionFactory()
    try:
        opened = _open_scan_job(session, automation_id)
    finally:
        session.close()
        SessionFactory.remove()

    if opened is None:
        logger.info("duplicate_scan: no indexed photos to scan")
        return
    job_id, file_paths = opened
    find_duplicates_task(job_id=job_id, file_paths=file_paths)


@register_handler(AUTOMATION_HANDLER_DUPLICATE_SCAN)
def enqueue_duplicate_scan(automation: Automation, context: EventContext | None = None) -> None:
    """Handler for the built-in duplicate-scan automation: enqueue the task tagged
    with the automation's id so its find_duplicates Job links back. `context` is
    unused (a full-library scan ignores any triggering event's subjects)."""
    duplicate_scan_task(automation.id)
