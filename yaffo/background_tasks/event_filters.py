"""Per-trigger event filters and the settle window.

An event trigger's `config` may narrow which events wake it. Each event type that
supports filters has one small matcher; later filters (e.g. `media_labeled` by label)
add a matcher of the same shape.

- `album_changed`: `album_id` (only this album) and `changes` (only these kinds).
- `media_deleted`: `sync_automation_id` (only deletes that queued removals for this
  sync, the trigger every target sync owns).

`settle_seconds` defers the run instead of starting it: dispatch sets the trigger's
`next_run_at` (only if it isn't already set), and the schedule dispatcher's tick
starts the run once it's due. A burst of changes therefore becomes one run.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Callable

from sqlalchemy import update
from sqlalchemy.orm import Session

from yaffo.background_tasks.events import EventContext
from yaffo.db.models import (
    AutomationTrigger, ALBUM_CHANGE_DELETED, EVENT_ALBUM_CHANGED, EVENT_MEDIA_DELETED,
)
from yaffo.utils.time import utcnow

CONFIG_ALBUM_ID = "album_id"
CONFIG_CHANGES = "changes"
CONFIG_SYNC_AUTOMATION_ID = "sync_automation_id"
CONFIG_SETTLE_SECONDS = "settle_seconds"


def _album_changed(config: dict, context: EventContext) -> bool:
    album_id = config.get(CONFIG_ALBUM_ID)
    if album_id is not None and album_id != context.album_id:
        return False
    changes = config.get(CONFIG_CHANGES)
    return not changes or context.change in changes


def _media_deleted(config: dict, context: EventContext) -> bool:
    sync_id = config.get(CONFIG_SYNC_AUTOMATION_ID)
    return sync_id is None or sync_id in context.sync_automation_ids


MATCHERS: dict[str, Callable[[dict, EventContext], bool]] = {
    EVENT_ALBUM_CHANGED: _album_changed,
    EVENT_MEDIA_DELETED: _media_deleted,
}


def trigger_matches(trigger: AutomationTrigger, context: EventContext) -> bool:
    matcher = MATCHERS.get(trigger.event_type or "")
    return matcher is None or matcher(trigger.config or {}, context)


def settle_seconds(trigger: AutomationTrigger, context: EventContext) -> int | None:
    """The deferral for this trigger, or None to dispatch now. A deleted album skips
    it, so the sync pauses (`selection_missing`) straight away."""
    seconds = (trigger.config or {}).get(CONFIG_SETTLE_SECONDS)
    if not seconds or (context.event_type == EVENT_ALBUM_CHANGED and context.change == ALBUM_CHANGE_DELETED):
        return None
    return int(seconds)


def defer(session: Session, trigger_id: int, seconds: int) -> bool:
    """Schedule the trigger's run `seconds` from now unless one is already pending.
    One conditional UPDATE, so concurrent events can't race. Commits."""
    result = session.execute(
        update(AutomationTrigger)
        .where(AutomationTrigger.id == trigger_id, AutomationTrigger.next_run_at.is_(None))
        .values(next_run_at=utcnow() + timedelta(seconds=seconds))
    )
    session.commit()
    return bool(result.rowcount)
