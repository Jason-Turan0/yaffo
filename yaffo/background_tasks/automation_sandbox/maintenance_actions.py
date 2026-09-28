"""Maintenance host functions: fixes the assistant can propose for the library and
the app, beyond editing metadata. Assistant profile only (automations don't get
them), and like every mutation they only run when the user approves a plan.

Some start background work rather than finishing in the approval request:
reindex_media and regenerate_thumbnails return a Job id (its HostFunction has `starts_job`). run_automation
queues a run whose worker creates the Job, so its plan links to the automation's
Run history. The assistant checks the resulting job afterwards.

Each ships with a `summarize_*` (the card's English fallback; the card words steps
from plans.step_facts in the user's language) and, where the step can already be
refused, a precondition checked when the plan is recorded and again at approval.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Optional

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_dispatch import invoke_automation
from yaffo.background_tasks.automation_sandbox.automation_actions import _emit_media_modified
from yaffo.background_tasks.automation_sandbox.host_types import HostCall
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.events import EventContext
from yaffo.background_tasks.schedule_scope import media_item_ids, selected_paths
from yaffo.db.models import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    Job,
    MediaItem,
)
from yaffo.db.repositories import automation_repository, media_repository, person_repository
from yaffo.taskq.store import STATUS_READY, STATUS_RUNNING
from yaffo.utils.index_jobs import reindex_media_items
from yaffo.utils.settings import get_thumbnail_dir
from yaffo.utils.thumbnail_marker import ensure_thumbnail_dir
from yaffo.utils.thumbnail_repair import find_missing_thumbnails, start_thumbnail_repair

# Queue tasks that hold faces PROCESSING while they assign them.
FACE_TASK_NAMES = ["assign_faces_to_person", "auto_assign_faces_automation_task"]


# ---- preconditions ---------------------------------------------------------------

def thumbnails_configured(args: list[Any], session: Session) -> str | None:
    return None if get_thumbnail_dir(session) is not None else "No thumbnail folder is configured"


def automation_exists(args: list[Any], session: Session) -> str | None:
    return None if automation_repository.get_by_slug(session, args[0]) else "No automation with that slug"


def automation_runnable(args: list[Any], session: Session) -> str | None:
    automation = automation_repository.get_by_slug(session, args[0])
    if automation is None:
        return "No automation with that slug"
    if not automation.handler and not automation.published_code:
        return "That automation has no runnable handler or published code"
    try:
        resolve_run_scope(session, automation.handler, args[1] if len(args) > 1 else None)
    except ValueError as exc:
        return str(exc)
    return None


def thumbnails_missing(args: list[Any], session: Session) -> str | None:
    missing = find_missing_thumbnails(session, get_thumbnail_dir(session))
    if not missing.available:
        return "The thumbnail folder isn't available"
    return None if missing.media_item_ids else "No thumbnails are missing"


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


# ---- regenerate_thumbnails -------------------------------------------------------------

def regenerate_thumbnails(session: Session) -> Annotated[str, "The id of the repair job."]:
    """Write the face crops and video posters whose files are gone again, from the
    photos and videos. Faces keep their people and ignored status."""
    missing = find_missing_thumbnails(session, get_thumbnail_dir(session))
    if not missing.media_item_ids:
        raise ValueError("No thumbnails are missing")
    return start_thumbnail_repair(session, missing)


def missing_thumbnail_count(session: Session) -> int:
    return find_missing_thumbnails(session, get_thumbnail_dir(session)).total


def summarize_regenerate_thumbnails(args: list[Any], session: Session) -> str:
    return f"Regenerate {missing_thumbnail_count(session)} missing thumbnail(s)"


# ---- cancel_job -------------------------------------------------------------------

def job_label(job: Job | None) -> str | None:
    """How a job is named to the user: its automation's name for a run, else the
    job's own name."""
    if job is None:
        return None
    return job.automation.display_name if job.automation is not None else job.name


def job_active(args: list[Any], session: Session) -> str | None:
    job = session.get(Job, args[0]) if args and isinstance(args[0], str) else None
    if job is None:
        return "Job no longer exists"
    if job.status not in (JOB_STATUS_PENDING, JOB_STATUS_RUNNING):
        return f"Job already finished ({job.status.lower()})"
    return None


def cancel_job(session: Session, job_id: str) -> None:
    """Cancel a pending or running job, as its Cancel button does. Its tasks stop
    at their next cancellation check; work already done stays done."""
    job = session.get(Job, job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id!r}")
    if job.status in (JOB_STATUS_PENDING, JOB_STATUS_RUNNING):
        job.status = JOB_STATUS_CANCELLED
        session.commit()


def summarize_cancel_job(args: list[Any], session: Session) -> str:
    name = job_label(session.get(Job, args[0])) if args and isinstance(args[0], str) else None
    return f"Cancel job '{name}'" if name else "Cancel a job"


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


# ---- run_automation -------------------------------------------------------------------

def resolve_run_scope(session: Session, handler: str | None, scope: dict | None) -> tuple[EventContext, str]:
    """Resolve a reviewed assistant scope against the current configured roots."""
    if scope is None:
        scope = {"type": "everything"}
    if not isinstance(scope, dict):
        raise ValueError("Automation scope must be an object")
    kind = scope.get("type")
    field = {"everything": None, "media_dirs": "media_dir_ids",
             "files": "media_item_ids", "folders": "folder_paths"}.get(kind)
    if kind not in {"everything", "media_dirs", "files", "folders"}:
        raise ValueError("Choose everything, media_dirs, files, or folders as the automation scope")
    if set(scope) != ({"type", field} if field else {"type"}):
        raise ValueError("Automation scope has unexpected or missing fields")
    if kind == "everything":
        paths = selected_paths(session, {"scope_type": "everything"})
        return EventContext(event_type=None, media_item_ids=media_item_ids(session, paths),
                            scope_paths=[str(path) for path in paths]), "all media folders"
    values = scope[field]
    if not isinstance(values, list) or not values or len(values) > 500:
        raise ValueError("Choose between 1 and 500 entries for the automation scope")
    if len(set(str(value) for value in values)) != len(values):
        raise ValueError("Automation scope contains duplicate entries")
    if kind == "files":
        if handler == "file_sync":
            raise ValueError("File sync needs media directories or folders, not individual files")
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("File scope needs indexed media item IDs")
        files = media_repository.get_paths_by_ids(session, values)
        if len(files) != len(values):
            raise ValueError("One or more selected files are no longer indexed")
        roots = selected_paths(session, {"scope_type": "everything"})
        if any(not any(Path(path).resolve().is_relative_to(root) for root in roots)
               for path in files.values()):
            raise ValueError("A selected file is outside configured media directories")
        return EventContext(event_type=None, media_item_ids=sorted(values)), \
            ", ".join(files[item_id] for item_id in values)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("Scope entries must be non-empty strings")
    config = ({"scope_type": "media_dirs", "media_dir_ids": values} if kind == "media_dirs"
              else {"scope_type": "paths", "folder_paths": values})
    paths = selected_paths(session, config)
    if kind == "folders" and any(path.exists() and not path.is_dir() for path in paths):
        raise ValueError("Folder scope must contain directories, not files")
    return EventContext(event_type=None, media_item_ids=media_item_ids(session, paths),
                        scope_paths=[str(path) for path in paths]), ", ".join(str(path) for path in paths)


def run_automation(session: Session, slug: str, scope: Optional[dict] = None) -> None:
    """Queue a scoped run; its worker records the Job."""
    automation = automation_repository.get_by_slug(session, slug)
    if automation is None:
        raise ValueError(f"No automation with slug {slug!r}")
    context, _ = resolve_run_scope(session, automation.handler, scope)
    if not invoke_automation(automation, context):
        raise ValueError(f"Automation {slug!r} has no runnable handler or published code")


def summarize_run_automation(args: list[Any], session: Session) -> str:
    automation = automation_repository.get_by_slug(session, args[0]) if args else None
    name = automation.display_name if automation else (args[0] if args else "")
    _, scope = resolve_run_scope(session, automation.handler if automation else None,
                                 args[1] if len(args) > 1 else None)
    return f"Run automation '{name}' for {scope}"


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
