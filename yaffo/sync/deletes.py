"""Photos deleted from Yaffo are removed from the targets they were sent to.

The removal can't happen inside the delete (it must be instant and never fail on a
slow service, bulk deletes would make one call per photo, and it would bypass the
one-run-per-sync lock). So the delete's own transaction marks the photo's target
rows `removing` (queue_target_removals), and after the commit `media_deleted` names
the syncs that now have work (announce_deleted); each sync's trigger schedules a run
through the settle window. See docs/development/automation-sync-adapters.md
(Photos deleted from Yaffo).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from yaffo.background_tasks.events import emit_event
from yaffo.db.models import EVENT_MEDIA_DELETED
from yaffo.db.repositories import sync_repository


def queue_target_removals(session: Session, media_item_ids: list[int]) -> list[int]:
    """Call inside the delete's transaction, before the media rows go."""
    return sync_repository.mark_removing_for_deleted_media(session, media_item_ids)


def announce_deleted(media_item_ids: list[int], sync_automation_ids: list[int]) -> None:
    """Call after the delete committed."""
    if media_item_ids:
        emit_event(EVENT_MEDIA_DELETED, {"media_item_ids": list(media_item_ids),
                                         "sync_automation_ids": list(sync_automation_ids)})
