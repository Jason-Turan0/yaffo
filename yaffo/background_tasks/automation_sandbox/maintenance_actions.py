"""Maintenance host functions: fixes the assistant can propose for the library and
the app, beyond editing metadata. Assistant profile only (automations don't get
them), and like every mutation they only run when the user approves a plan.

Some start background work rather than finishing in the approval request:
start_library_scan, index_files and reindex_media return a Job id (their
HostFunction has `starts_job`), and the work goes on after the plan says "done".
The assistant checks the job afterwards (job_detail). The rest finish immediately.

A sync is two plans, not one guess: start_library_scan records exactly which
files aren't indexed and which items lost their file; then index_files and
remove_missing_items act on that scan. So the card for removing items names the
exact count, and the user, who knows whether they deleted a folder, decides.
Each missing item is checked again at approval, and one whose file is back stays.

Each ships with a `summarize_*` (the card's English fallback; the card words steps
from plans.step_facts in the user's language) and, where the step can already be
refused, a precondition checked when the plan is recorded and again at approval.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Optional

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.automation_actions import _emit_media_modified
from yaffo.background_tasks.automation_sandbox.host_types import HostCall
from yaffo.background_tasks.config import task_queue
from yaffo.db.models import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_PENDING,
    MEDIA_STATUS_INDEXED,
    Job,
    MediaItem,
)
from yaffo.db.repositories import automation_repository, person_repository
from yaffo.db.repositories.media_dir_repository import get_media_dirs
from yaffo.taskq.store import STATUS_READY, STATUS_RUNNING
from yaffo.utils.file_sync import ORPHAN_MISSING, orphan_reason, root_has_media
from yaffo.utils.index_jobs import enqueue_index_jobs, reindex_media_items
from yaffo.utils.index_photos import delete_orphaned_media_items, delete_orphaned_thumbnails
from yaffo.utils.settings import get_thumbnail_dir
from yaffo.utils.thumbnail_marker import ensure_thumbnail_dir
from yaffo.utils.time import utcnow

# Queue tasks that hold faces PROCESSING while they assign them.
FACE_TASK_NAMES = ["assign_faces_to_person", "auto_assign_faces_automation_task"]

# The Job tasks/library_scan.py runs.
JOB_NAME_SCAN = "library_scan"
# A scan's findings are acted on for this long; after that, scan again.
SCAN_MAX_AGE = timedelta(hours=24)


# ---- preconditions ---------------------------------------------------------------

def thumbnails_configured(args: list[Any], session: Session) -> str | None:
    return None if get_thumbnail_dir(session) is not None else "No thumbnail folder is configured"


def library_scannable(args: list[Any], session: Session) -> str | None:
    if not get_media_dirs(session):
        return "No media folders are configured"
    return thumbnails_configured(args, session)


def automation_exists(args: list[Any], session: Session) -> str | None:
    return None if automation_repository.get_by_slug(session, args[0]) else "No automation with that slug"


def faces_need_repair(args: list[Any], session: Session) -> str | None:
    return None if face_repair_counts(session).total else "No faces need repairing"


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


# ---- start_library_scan ------------------------------------------------------------------

def start_library_scan(session: Session) -> Annotated[str, "The id of the scan job; its message has the result."]:
    """Compare the media folders with the index in the background: which files
    aren't indexed yet, and which indexed items lost their file. Changes nothing."""
    # In-function: importing the tasks package here would import the host API back.
    from yaffo.background_tasks.tasks.library_scan import library_scan_task
    job_id = str(uuid.uuid4())
    session.add(Job(id=job_id, name=JOB_NAME_SCAN, status=JOB_STATUS_PENDING, task_count=1,
                    completed_count=0, error_count=0, cancelled_count=0))
    session.commit()
    library_scan_task(job_id)
    return job_id


def summarize_start_library_scan(args: list[Any], session: Session) -> str:
    return "Scan the media folders for new and missing files"


# ---- acting on a scan: index_files / remove_missing_items ------------------------------

def scan_findings(session: Session, scan_job_id: Any) -> dict | None:
    """A finished, recent scan's job_data, or None."""
    job = session.get(Job, scan_job_id) if isinstance(scan_job_id, str) else None
    if job is None or job.name != JOB_NAME_SCAN or job.status != JOB_STATUS_COMPLETED:
        return None
    if job.completed_at is None or utcnow() - job.completed_at > SCAN_MAX_AGE:
        return None
    try:
        return json.loads(job.job_data or "{}")
    except ValueError:
        return None


def _scan_problem(session: Session, scan_job_id: Any) -> str | None:
    job = session.get(Job, scan_job_id) if isinstance(scan_job_id, str) else None
    if job is None or job.name != JOB_NAME_SCAN:
        return "No scan job with that id; start one with start_library_scan"
    if job.status != JOB_STATUS_COMPLETED:
        return "That scan hasn't finished (or failed); check it with job_detail"
    if scan_findings(session, scan_job_id) is None:
        return "That scan is more than a day old; start a new one"
    return None


def files_to_index(session: Session, scan_job_id: Any) -> list[str]:
    """The scan's unindexed files that still exist and still aren't indexed."""
    findings = scan_findings(session, scan_job_id) or {}
    paths = list(dict.fromkeys(findings.get("unindexed_paths") or []))
    if not paths:
        return []
    indexed = {path for (path,) in session.query(MediaItem.full_file_path)
               .filter(MediaItem.full_file_path.in_(paths), MediaItem.status == MEDIA_STATUS_INDEXED)}
    return [path for path in paths if path not in indexed and Path(path).exists()]


def scan_has_files(args: list[Any], session: Session) -> str | None:
    problem = _scan_problem(session, args[0])
    if problem:
        return problem
    if not files_to_index(session, args[0]):
        return "That scan found no files left to index"
    return thumbnails_configured(args, session)


def index_files(session: Session, scan_job_id: str) -> Annotated[str, "The id of the index job."]:
    """Index the files a scan found not yet indexed (new files, or ones whose
    earlier import or index failed)."""
    files = files_to_index(session, scan_job_id)
    if not files:
        raise ValueError("That scan found no files left to index")
    ensure_thumbnail_dir(get_thumbnail_dir(session))
    return enqueue_index_jobs(session, files).index_job_id


def summarize_index_files(args: list[Any], session: Session) -> str:
    return f"Index {len(files_to_index(session, args[0])) if args else 0} file(s) found by the scan"


def scan_missing_ids(session: Session, scan_job_id: Any, media_item_ids: Optional[list[int]] = None) -> list[int]:
    """The scan's missing items, narrowed to `media_item_ids` when given."""
    findings = scan_findings(session, scan_job_id) or {}
    missing = [entry["id"] for entry in findings.get("missing") or []]
    if media_item_ids is None:
        return missing
    wanted = set(media_item_ids)
    return [item_id for item_id in missing if item_id in wanted]


def still_missing(session: Session, media_item_ids: list[int]) -> list[int]:
    """Of these items, the ones whose file is still gone right now. An item under
    a media folder that is now missing or empty (a drive that isn't mounted)
    doesn't count: its file may well come back."""
    media_dirs = get_media_dirs(session)
    live_roots = {str(root) for root in media_dirs if root.exists() and root_has_media(root)}
    result = []
    for item_id, path in session.query(MediaItem.id, MediaItem.full_file_path).filter(
            MediaItem.id.in_(list(media_item_ids))):
        reason = orphan_reason(Path(path), media_dirs)
        if reason is None:
            continue
        if reason == ORPHAN_MISSING and not any(
                Path(path).expanduser().is_relative_to(Path(root).expanduser()) for root in live_roots):
            continue
        result.append(item_id)
    return result


def scan_has_missing(args: list[Any], session: Session) -> str | None:
    problem = _scan_problem(session, args[0])
    if problem:
        return problem
    subset = args[1] if len(args) > 1 else None
    if subset is not None and not isinstance(subset, list):
        return "media_item_ids must be a list of ids"
    if not scan_missing_ids(session, args[0], subset):
        return "That scan found none of these items missing"
    return None


def remove_missing_items(
    session: Session, scan_job_id: str, media_item_ids: Optional[list[int]] = None,
) -> Annotated[int, "How many items were removed."]:
    """Remove from the library the items a scan found whose file is gone (all of
    them, or those of `media_item_ids`). Their faces, people links, tags and album
    entries go too; the files are not touched. Items whose file is back are kept."""
    ids = still_missing(session, scan_missing_ids(session, scan_job_id, media_item_ids))
    removed = delete_orphaned_media_items(session, ids)
    thumbnail_dir = get_thumbnail_dir(session)
    if thumbnail_dir is not None and thumbnail_dir.exists():
        delete_orphaned_thumbnails(session, thumbnail_dir)
    return removed


def summarize_remove_missing_items(args: list[Any], session: Session) -> str:
    ids = scan_missing_ids(session, args[0], args[1] if len(args) > 1 else None) if args else []
    return f"Remove {len(ids)} item(s) whose files are gone"


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
