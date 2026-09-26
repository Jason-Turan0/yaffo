"""Background task: scan the media folders against the index.

The legacy assistant scan helper in automation_sandbox/maintenance_actions.py
created these jobs. A scan walks every media folder, which can take minutes on a
large library or a failing drive, so it runs in the task queue.

The scan changes nothing. It is the first half of a sync: the caller creates the
Job; this task runs the scan and records what it found on the Job. `message` is the
summary (read back by the assistant's job_detail); `job_data` holds the exact
lists, the files not yet indexed and the items whose file is gone, which a later
plan acts on (index_files, remove_missing_items). So the user approves removing
those exact items, knowing whether they deleted a folder, instead of a sync
guessing from counts.

Items under a media folder that came back empty (an empty mount point, usually)
are left out of the missing list and named in the summary instead.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, JOB_STATUS_RUNNING, Job
from yaffo.db.repositories.media_dir_repository import get_media_dirs
from yaffo.logging_config import get_logger
from yaffo.utils.file_sync import MediaScan, scan_media_dirs, under_empty_root
from yaffo.utils.settings import get_thumbnail_dir
from yaffo.utils.time import utcnow

logger = get_logger(__name__, 'background_tasks')

# Example paths listed in a scan's message.
SAMPLE_PATHS = 10


def _summary(scan: MediaScan, missing: list[dict], held_back: int) -> str:
    lines = [
        f"{len(scan.unindexed)} file(s) in the media folders aren't indexed yet; "
        f"{len(missing)} indexed item(s) no longer have their file "
        f"(library: {scan.total_imported} item(s), {scan.total_filesystem} media file(s) on disk)."
    ]
    if held_back:
        lines.append(
            f"These media folders hold no media files at all, which usually means a drive didn't mount: "
            f"{', '.join(scan.empty_roots)}. Their {held_back} indexed item(s) aren't counted as missing.")
    if scan.unindexed:
        lines.append("Not indexed, e.g.: " + "; ".join(u["full_path"] for u in scan.unindexed[:SAMPLE_PATHS]))
    if missing:
        lines.append("File missing, e.g.: " + "; ".join(
            f"{o['full_path']} ({o['reason']})" for o in missing[:SAMPLE_PATHS]))
    return "\n".join(lines)


def _finish(session: Session, job: Job, status: str, message: str, data: dict, error: str | None = None) -> None:
    job.status = status
    job.message = message
    job.error = error
    job.job_data = json.dumps(data)
    job.completed_count = 1 if status == JOB_STATUS_COMPLETED else 0
    job.error_count = 0 if status == JOB_STATUS_COMPLETED else 1
    job.completed_at = utcnow()
    session.commit()


def run_library_scan(session: Session, job_id: str) -> None:
    """Run the scan Job `job_id` and record its findings on it."""
    job = session.get(Job, job_id)
    if job is None:
        logger.warning(f"library_scan: job {job_id} not found")
        return
    job.status, job.started_at = JOB_STATUS_RUNNING, utcnow()
    session.commit()

    media_dirs = get_media_dirs(session)
    thumbnail_dir = get_thumbnail_dir(session)
    if not media_dirs:
        _finish(session, job, JOB_STATUS_FAILED, "No media folders are configured.", {},
                error="No media folders are configured.")
        return

    try:
        scan = scan_media_dirs(session, media_dirs, thumbnail_dir)
    except Exception as exc:  # a failing drive: record it, don't crash the worker
        logger.error(f"library_scan: job {job_id} scan failed: {exc}", exc_info=True)
        _finish(session, job, JOB_STATUS_FAILED, "The scan failed.", {}, error=f"The scan failed: {exc}")
        return

    missing = [o for o in scan.orphaned if not under_empty_root(o["full_path"], scan.empty_roots)]
    _finish(session, job, JOB_STATUS_COMPLETED, _summary(scan, missing, len(scan.orphaned) - len(missing)), {
        "unindexed_paths": scan.files_to_index,
        "missing": [{"id": o["id"], "reason": o["reason"]} for o in missing],
        "empty_roots": scan.empty_roots,
        "total_items": scan.total_imported,
        "total_files": scan.total_filesystem,
    })


@task_queue.task()
def library_scan_task(job_id: str) -> None:
    session = SessionFactory()
    try:
        run_library_scan(session, job_id)
    finally:
        session.close()
        SessionFactory.remove()
