"""Maintenance host functions: fixes the assistant can propose for the library and
the app, beyond editing metadata. Assistant profile only (automations don't get
them), and like every mutation they only run when the user approves a plan.

Most start background work rather than finishing in the approval request:
retry_job, reindex_media, start_library_scan and run_sync return a Job id (their
HostFunction has `starts_job`), and the work goes on after the plan says "done".
The assistant checks the job afterwards (job_detail). set_automation_enabled and
repair_face_statuses finish immediately.

Each ships with a `summarize_*` (the card's English fallback; the card words steps
from plans.step_facts in the user's language) and, where the step can already be
refused, a precondition checked when the plan is recorded and again at approval.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Annotated, Any, Optional

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.automation_actions import _emit_media_modified
from yaffo.background_tasks.automation_sandbox.host_types import HostCall
from yaffo.background_tasks.config import task_queue
from yaffo.db.models import (
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    MEDIA_STATUS_INDEXED,
    Job,
    MediaItem,
)
from yaffo.db.repositories import automation_repository, person_repository
from yaffo.db.repositories.media_dir_repository import get_media_dirs
from yaffo.taskq.store import STATUS_READY, STATUS_RUNNING
from yaffo.utils.index_jobs import enqueue_index_jobs, reindex_media_items
from yaffo.utils.settings import get_thumbnail_dir
from yaffo.utils.thumbnail_marker import ensure_thumbnail_dir

# Queue tasks that hold faces PROCESSING while they assign them.
FACE_TASK_NAMES = ["assign_faces_to_person", "auto_assign_faces_automation_task"]
# Jobs whose failed files retry_job can queue again.
RETRYABLE_JOB_NAMES = ("import_photos", "index_photos")

# The Jobs tasks/library_scan.py runs.
JOB_NAME_SCAN = "library_scan"
JOB_NAME_SYNC = "library_sync"


# ---- preconditions ---------------------------------------------------------------

def thumbnails_configured(args: list[Any], session: Session) -> str | None:
    return None if get_thumbnail_dir(session) is not None else "No thumbnail folder is configured"


def library_scannable(args: list[Any], session: Session) -> str | None:
    if not get_media_dirs(session):
        return "No media folders are configured"
    return thumbnails_configured(args, session)


def library_syncable(args: list[Any], session: Session) -> str | None:
    missing = [str(d) for d in get_media_dirs(session) if not d.exists()]
    if missing:
        return f"These media folders aren't connected: {', '.join(missing)}"
    return library_scannable(args, session)


def automation_exists(args: list[Any], session: Session) -> str | None:
    return None if automation_repository.get_by_slug(session, args[0]) else "No automation with that slug"


def job_retryable(args: list[Any], session: Session) -> str | None:
    job = session.get(Job, args[0]) if isinstance(args[0], str) else None
    if job is None:
        return "No job with that id"
    if job.name not in RETRYABLE_JOB_NAMES:
        return "Only import and index jobs can be retried"
    if job.status in (JOB_STATUS_PENDING, JOB_STATUS_RUNNING):
        return "The job is still running"
    if not retry_files(session, job):
        return "Every file in that job is already indexed"
    return thumbnails_configured(args, session)


def faces_need_repair(args: list[Any], session: Session) -> str | None:
    return None if face_repair_counts(session).total else "No faces need repairing"


# ---- retry_job ----------------------------------------------------------------------

def retry_files(session: Session, job: Job) -> list[str]:
    """The files of an import/index job that still aren't indexed: those that
    failed, or never ran because the job was cancelled."""
    try:
        data = json.loads(job.job_data or "{}")
    except ValueError:
        return []
    files = list(dict.fromkeys(data.get("files_to_import") or data.get("files_to_index") or []))
    if not files:
        return []
    indexed = {path for (path,) in session.query(MediaItem.full_file_path)
               .filter(MediaItem.full_file_path.in_(files), MediaItem.status == MEDIA_STATUS_INDEXED)}
    return [path for path in files if path not in indexed]


def retry_job(session: Session, job_id: str) -> Annotated[str, "The id of the new index job."]:
    """Queue the failed (or never-run) files of an import or index job again."""
    job = session.get(Job, job_id)
    files = retry_files(session, job) if job is not None else []
    if not files:
        raise ValueError("Nothing in that job is left to retry")
    ensure_thumbnail_dir(get_thumbnail_dir(session))
    return enqueue_index_jobs(session, files).index_job_id


def summarize_retry_job(args: list[Any], session: Session) -> str:
    job = session.get(Job, args[0]) if args and isinstance(args[0], str) else None
    count = len(retry_files(session, job)) if job is not None else 0
    return f"Retry {count} file(s) from a failed job"


# ---- reindex_media --------------------------------------------------------------------

def reindex_media(session: Session, media_item_ids: list[int]) -> Annotated[str, "The id of the index job."]:
    """Index items again from their files. Their faces are detected again, so any
    person assignments on them are removed."""
    items = session.query(MediaItem).filter(MediaItem.id.in_(list(media_item_ids or []))).all()
    items = [item for item in items if item.full_file_path and Path(item.full_file_path).exists()]
    if not items:
        raise ValueError("None of these items' files exist")
    ensure_thumbnail_dir(get_thumbnail_dir(session))
    return reindex_media_items(session, items).index_job_id


def summarize_reindex_media(args: list[Any], session: Session) -> str:
    ids = args[0] if args and isinstance(args[0], list) else []
    return f"Re-index {len(ids)} item(s)"


# ---- start_library_scan / run_sync ------------------------------------------------------

def _start_scan_job(session: Session, name: str, apply: bool) -> str:
    # In-function: importing the tasks package here would import the host API back.
    from yaffo.background_tasks.tasks.library_scan import library_scan_task
    job_id = str(uuid.uuid4())
    session.add(Job(id=job_id, name=name, status=JOB_STATUS_PENDING, task_count=1,
                    completed_count=0, error_count=0, cancelled_count=0))
    session.commit()
    library_scan_task(job_id, apply)
    return job_id


def start_library_scan(session: Session) -> Annotated[str, "The id of the scan job; its message has the result."]:
    """Compare the media folders with the index in the background: which files
    aren't indexed yet, and which indexed items lost their file. Changes nothing."""
    return _start_scan_job(session, JOB_NAME_SCAN, apply=False)


def summarize_start_library_scan(args: list[Any], session: Session) -> str:
    return "Scan the media folders for new and missing files"


def run_sync(session: Session) -> Annotated[str, "The id of the sync job; its message has the result."]:
    """Scan, then index new files and remove items whose files are gone, like
    Index Photos → Sync. Refused when it would remove too much of the library."""
    return _start_scan_job(session, JOB_NAME_SYNC, apply=True)


def summarize_run_sync(args: list[Any], session: Session) -> str:
    return "Sync the library with the media folders"


# ---- set_automation_enabled -----------------------------------------------------------

def set_automation_enabled(session: Session, slug: str, enabled: bool, expected: Optional[bool] = None) -> None:
    """Turn an automation on or off. `expected` skips the change when the switch
    was flipped since (undo after a later edit)."""
    automation = automation_repository.get_by_slug(session, slug)
    if automation is None:
        raise ValueError(f"No automation with slug {slug!r}")
    if expected is not None and bool(automation.enabled) != bool(expected):
        return
    automation.enabled = bool(enabled)
    session.commit()


def summarize_set_automation_enabled(args: list[Any], session: Session) -> str:
    automation = automation_repository.get_by_slug(session, args[0]) if args else None
    name = automation.display_name if automation else (args[0] if args else "")
    return f"{'Turn on' if len(args) > 1 and args[1] else 'Turn off'} automation '{name}'"


def undo_set_automation_enabled(args: list[Any], session: Session) -> list[HostCall]:
    automation = automation_repository.get_by_slug(session, args[0])
    if automation is None or bool(automation.enabled) == bool(args[1]):
        return []
    return [HostCall("set_automation_enabled", [args[0], bool(automation.enabled), bool(args[1])])]


# ---- repair_face_statuses ----------------------------------------------------------------

def face_tasks_active() -> bool:
    counts = task_queue.store.status_counts(FACE_TASK_NAMES)
    return bool(counts.get(STATUS_READY, 0) + counts.get(STATUS_RUNNING, 0))


def face_repair_counts(session: Session) -> person_repository.FaceStatusProblems:
    """What repair_face_statuses would change now: stuck PROCESSING faces only
    while no face task is queued or running."""
    problems = person_repository.face_status_problems(session)
    if face_tasks_active():
        return person_repository.FaceStatusProblems(problems.linked_not_assigned, 0, 0, problems.ignored_linked)
    return problems


def repair_face_statuses(session: Session) -> None:
    """Fix faces left in inconsistent states: linked but not ASSIGNED, stuck
    PROCESSING (only while no face task runs), IGNORED but still linked (the link
    goes). Same rules as migrations 009 and 010."""
    _, media_item_ids = person_repository.repair_face_statuses(
        session, include_processing=not face_tasks_active())
    _emit_media_modified(media_item_ids)


def summarize_repair_face_statuses(args: list[Any], session: Session) -> str:
    return f"Repair {face_repair_counts(session).total} face(s) in inconsistent states"
