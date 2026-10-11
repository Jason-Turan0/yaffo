import contextvars
import json
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from yaffo.db.models import (
    Job, MediaItem,
    EVENT_ALBUM_CHANGED, EVENT_MEDIA_IMPORTED, EVENT_MEDIA_INDEXED,
)
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')


@dataclass(frozen=True)
class EventContext:
    """What a domain event hands to a subscribed automation's handler. Built by
    dispatch_event_task from the queued (event_type, payload); `media_item_ids` are the
    subjects the event concerns (empty when an event has no media subjects).

    `groups` carries related-photo groupings when an event provides them — e.g.
    duplicates_found passes one list of photo ids per duplicate set (each ordered
    earliest-indexed first). Empty for events without groupings.

    `origin_automation_ids` is the loop guard's **causal chain**: the automations that
    have already fired in the chain of events leading to this one. dispatch_event_task
    skips any automation already in it, so an automation fires at most once per chain
    (see event_chain_scope / the Loop guard section in docs/development/automations.md)."""
    event_type: str | None
    job_id: str | None = None
    media_item_ids: list[int] = field(default_factory=list)
    groups: list[list[int]] = field(default_factory=list)
    origin_automation_ids: list[int] = field(default_factory=list)
    scope_paths: list[str] = field(default_factory=list)
    # album_changed only: which album, and how it changed (added | removed | renamed | deleted).
    album_id: int | None = None
    change: str | None = None
    # media_deleted only: the sync automations that had the photos' copies queued for removal.
    sync_automation_ids: list[int] = field(default_factory=list)
    # device_online only: which paired device appeared, and where (hub | lan).
    device_id: str | None = None


# The causal chain in force for the current automation run, set by event_chain_scope.
# emit_event reads it to stamp every event emitted during the run — explicitly by the
# task, or implicitly from inside a host action — without threading ids to each call
# site (a host action's emit has no automation id in scope). contextvars nest with
# reset tokens, so a nested run (immediate-mode recursive dispatch) layers correctly.
_event_chain: contextvars.ContextVar[tuple[int, ...]] = contextvars.ContextVar(
    "event_chain", default=()
)


@contextmanager
def event_chain_scope(origin_automation_ids: list[int] | None, automation_id: int):
    """Establish the causal chain for the duration of an automation run: events emitted
    inside carry (origin + this automation), so the loop guard can break a cycle that
    would re-trigger this automation. Wrap the run's emit-bearing region with it."""
    chain = tuple(origin_automation_ids or ()) + (automation_id,)
    token = _event_chain.set(chain)
    try:
        yield
    finally:
        _event_chain.reset(token)


# event_type stamped on the EventContext of a manual run over a picked
# file/folder (no domain event fired it). Handlers act on media_item_ids regardless;
# custom scripts can read ctx['event_type'] to tell a manual run from a real event.
MANUAL_RUN_EVENT_TYPE = "manual"


# Which completed Job names emit which event (per-job-completion granularity).
# Jobs not listed emit nothing. (duplicates_found is emitted directly from
# find_duplicates_task, which carries the duplicate groups, not via this map.)
JOB_EVENT_MAP: dict[str, str] = {
    'index_photos': EVENT_MEDIA_INDEXED,
    'import_photos': EVENT_MEDIA_IMPORTED,
}


def emit_event(event_type: str, payload: dict) -> None:
    """Fire a domain event: enqueue a single dispatch task that fans it out to
    subscribed automations. The task import is in-function (like
    schedule_job_completion) so this module never imports the tasks package.

    The current causal chain (event_chain_scope) is stamped onto the payload as
    `origin_automation_ids` so the loop guard in dispatch_event_task can break cycles;
    emitted outside any run (routes, job completion) the chain is empty — a genuine
    origin."""
    from yaffo.background_tasks.tasks.dispatch_event import dispatch_event_task
    payload = {**payload, "origin_automation_ids": list(_event_chain.get())}
    dispatch_event_task(event_type, payload)


# An album_changed payload names at most this many photos (the data_query cap);
# beyond it, or when a bulk operation didn't list them, it says `truncated`.
ALBUM_EVENT_ID_CAP = 5000


def emit_album_changed(album_id: int, change: str, media_item_ids: list[int] | None = None) -> None:
    """album_changed for one operation on an album (not one per photo). Call after
    the change committed. `media_item_ids` None means "not listed" (a bulk add or
    remove by filter): subscribers that need exactness re-read the album."""
    ids = list(media_item_ids or [])
    truncated = media_item_ids is None or len(ids) > ALBUM_EVENT_ID_CAP
    emit_event(EVENT_ALBUM_CHANGED, {"album_id": album_id, "change": change,
                                     "media_item_ids": ids[:ALBUM_EVENT_ID_CAP], "truncated": truncated})


def _resolve_media_item_ids(session: Session, job: Job) -> list[int]:
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
    return [pid for (pid,) in session.query(MediaItem.id).filter(MediaItem.full_file_path.in_(files)).all()]


def emit_job_completed_event(session: Session, job: Job) -> None:
    """Emit the event (if any) for a job that just completed. Called from
    complete_job_task after the COMPLETED status is committed."""
    event_type = JOB_EVENT_MAP.get(job.name)
    if event_type is None:
        return
    emit_event(event_type, {
        'job_id': job.id,
        'media_item_ids': _resolve_media_item_ids(session, job),
    })
