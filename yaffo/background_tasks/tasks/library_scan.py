"""Background task: scan the media folders against the index, and optionally sync.

Started by the assistant's maintenance actions (start_library_scan, run_sync in
automation_sandbox/maintenance_actions.py), after the user approved them. A scan
walks every media folder, which can take minutes on a large library or a failing
drive, so it never runs in the web request that approved it.

The caller creates the Job; this task runs it: RUNNING, the scan, then COMPLETED
with what it found in `message` (read back by the assistant's job_detail) and
`job_data`, or FAILED with the reason. A sync then applies the scan through
perform_sync, the same code as the Index Photos Sync button, so its import/index
Jobs appear there like any other sync.

A sync refuses to run when it would remove too much of the library at once: an
unmounted or half-mounted drive makes whole folders look deleted (see the
mass-removal guard in docs/development/ai-assistant.md).
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, JOB_STATUS_RUNNING, Job
from yaffo.db.repositories.media_dir_repository import get_media_dirs
from yaffo.logging_config import get_logger
from yaffo.utils.file_sync import MediaScan, perform_sync, scan_media_dirs
from yaffo.utils.settings import get_thumbnail_dir
from yaffo.utils.thumbnail_marker import ensure_thumbnail_dir
from yaffo.utils.time import utcnow

logger = get_logger(__name__, 'background_tasks')

# A sync may always remove this many items, and beyond that at most this share of
# the library. More than that is far likelier a disconnected drive than deletions.
SYNC_REMOVE_FLOOR = 25
SYNC_MAX_REMOVE_SHARE = 0.10
# Example paths listed in a scan's message.
SAMPLE_PATHS = 10


def removal_limit(total_items: int) -> int:
    return max(SYNC_REMOVE_FLOOR, int(total_items * SYNC_MAX_REMOVE_SHARE))


def _summary(scan: MediaScan) -> str:
    lines = [
        f"{len(scan.unindexed)} file(s) in the media folders aren't indexed yet; "
        f"{len(scan.orphaned)} indexed item(s) no longer have a file under a configured media folder "
        f"(library: {scan.total_imported} item(s), {scan.total_filesystem} media file(s) on disk)."
    ]
    if scan.unindexed:
        lines.append("Not indexed, e.g.: " + "; ".join(u["full_path"] for u in scan.unindexed[:SAMPLE_PATHS]))
    if scan.orphaned:
        lines.append("File missing, e.g.: " + "; ".join(
            f"{o['full_path']} ({o['reason']})" for o in scan.orphaned[:SAMPLE_PATHS]))
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


def run_library_scan(session: Session, job_id: str, apply: bool) -> None:
    """Run the Job `job_id`: scan, and with `apply` sync what the scan found."""
    job = session.get(Job, job_id)
    if job is None:
        logger.warning(f"library_scan: job {job_id} not found")
        return
    job.status, job.started_at = JOB_STATUS_RUNNING, utcnow()
    session.commit()

    media_dirs = get_media_dirs(session)
    thumbnail_dir = get_thumbnail_dir(session)
    missing = [str(d) for d in media_dirs if not d.exists()]
    if not media_dirs or thumbnail_dir is None or (apply and missing):
        reason = ("No media folders are configured." if not media_dirs
                  else "No thumbnail folder is configured." if thumbnail_dir is None
                  else f"These media folders aren't connected: {', '.join(missing)}. Nothing was synced.")
        _finish(session, job, JOB_STATUS_FAILED, reason, {}, error=reason)
        return

    try:
        scan = scan_media_dirs(session, media_dirs, thumbnail_dir)
    except Exception as exc:  # a failing drive: record it, don't crash the worker
        logger.error(f"library_scan: job {job_id} scan failed: {exc}", exc_info=True)
        _finish(session, job, JOB_STATUS_FAILED, "The scan failed.", {}, error=f"The scan failed: {exc}")
        return

    data = {"unindexed": len(scan.unindexed), "orphaned": len(scan.orphaned),
            "total_items": scan.total_imported, "total_files": scan.total_filesystem}
    summary = _summary(scan)
    if not apply:
        _finish(session, job, JOB_STATUS_COMPLETED, summary, data)
        return

    limit = removal_limit(scan.total_imported)
    if len(scan.orphaned) > limit:
        reason = (f"Refused: the sync would remove {len(scan.orphaned)} of {scan.total_imported} item(s) from "
                  f"the library, more than the {limit} allowed at once. A disconnected or partly mounted "
                  "drive usually causes this. Check every media folder is connected, then sync from "
                  "Utilities → Index Photos, where the list can be reviewed first.")
        _finish(session, job, JOB_STATUS_FAILED, summary + "\n" + reason, {**data, "removal_limit": limit},
                error=reason)
        return
    if not scan.unindexed and not scan.orphaned:
        _finish(session, job, JOB_STATUS_COMPLETED, "Already in sync; nothing to do.", data)
        return

    ensure_thumbnail_dir(thumbnail_dir)
    jobs = perform_sync(session, scan.files_to_index, scan.orphaned_media_item_ids, thumbnail_dir)
    _finish(session, job, JOB_STATUS_COMPLETED,
            f"Removed {len(scan.orphaned)} item(s) whose files are gone, and started indexing "
            f"{len(scan.unindexed)} new file(s) (import job {jobs.import_job_id}, index job {jobs.index_job_id}).",
            {**data, "import_job_id": jobs.import_job_id, "index_job_id": jobs.index_job_id})


@task_queue.task()
def library_scan_task(job_id: str, apply: bool = False) -> None:
    session = SessionFactory()
    try:
        run_library_scan(session, job_id, apply)
    finally:
        session.close()
        SessionFactory.remove()
