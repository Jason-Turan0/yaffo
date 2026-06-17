import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from yaffo.db.models import (
    Job, Photo,
    EVENT_PHOTO_IMPORTED, EVENT_PHOTO_INDEXED,
)
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')


@dataclass(frozen=True)
class EventContext:
    """What a domain event hands to a subscribed automation's handler. Built by
    dispatch_event_task from the queued (event_type, payload); `photo_ids` are the
    subjects the event concerns (empty when an event has no photo subjects).

    `groups` carries related-photo groupings when an event provides them — e.g.
    duplicates_found passes one list of photo ids per duplicate set (each ordered
    earliest-indexed first). Empty for events without groupings."""
    event_type: str
    job_id: str | None = None
    photo_ids: list[int] = field(default_factory=list)
    groups: list[list[int]] = field(default_factory=list)


# event_type stamped on the EventContext of a manual "Run now" over a picked
# file/folder (no domain event fired it). Handlers act on photo_ids regardless;
# custom scripts can read ctx['event_type'] to tell a manual run from a real event.
MANUAL_RUN_EVENT_TYPE = "manual"


# Which completed Job names emit which event (per-job-completion granularity).
# Jobs not listed emit nothing. (duplicates_found is emitted directly from
# find_duplicates_task, which carries the duplicate groups, not via this map.)
JOB_EVENT_MAP: dict[str, str] = {
    'index_photos': EVENT_PHOTO_INDEXED,
    'import_photos': EVENT_PHOTO_IMPORTED,
}


def emit_event(event_type: str, payload: dict) -> None:
    """Fire a domain event: enqueue a single dispatch task that fans it out to
    subscribed automations. The task import is in-function (like
    schedule_job_completion) so this module never imports the tasks package."""
    from yaffo.background_tasks.tasks.dispatch_event import dispatch_event_task
    dispatch_event_task(event_type, payload)


def _resolve_photo_ids(session: Session, job: Job) -> list[int]:
    """Photo ids a completed import/index job touched, from its job_data files."""
    if not job.job_data:
        return []
    try:
        data = json.loads(job.job_data)
    except (TypeError, ValueError):
        return []
    files = data.get('files_to_index') or data.get('files_to_import') or []
    if not files:
        return []
    return [pid for (pid,) in session.query(Photo.id).filter(Photo.full_file_path.in_(files)).all()]


def emit_job_completed_event(session: Session, job: Job) -> None:
    """Emit the event (if any) for a job that just completed. Called from
    complete_job_task after the COMPLETED status is committed."""
    event_type = JOB_EVENT_MAP.get(job.name)
    if event_type is None:
        return
    emit_event(event_type, {
        'job_id': job.id,
        'photo_ids': _resolve_photo_ids(session, job),
    })