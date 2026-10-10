"""A target's selection -> the desired set: [{media_item_id, target, caption}].

`album`: the album's members, routed to the album's name. `query`: the photos
matching filter-panel selections, stored in the sharing wire format (validated,
distances in km, only the filters that are set), routed to the sync's name.
Selector scripts (`script`) run with read-only automation host functions.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from yaffo.db.models import Album, AlbumItem, MediaItem, MEDIA_STATUS_FAILED
from yaffo.background_tasks.automation_sandbox.automation_host import build_host_functions
from yaffo.background_tasks.automation_sandbox.starlark_runner import run_starlark
from yaffo.db.repositories.media_filter_repository import apply_media_filters
from yaffo.domain.media_filter_params import selections_from_wire, validate_wire_filters
from yaffo.sync.schemas import SELECTION_ALBUM, SELECTION_QUERY, SELECTION_SCRIPT, Selection

DESIRED_SET_LIMIT = 5000


class SelectionMissing(Exception):
    """The selected album no longer exists: pause rather than remove everything."""


class SelectionUnsupported(Exception):
    pass


@dataclass(frozen=True)
class Desired:
    media_item_id: int
    target: str
    caption: str | None = None


def validate_query(query: dict) -> str | None:
    return validate_wire_filters(query)


def resolve_selection(session: Session, selection: Selection, default_target: str,
                      script: str | None = None) -> list[Desired]:
    """The desired set, capped at DESIRED_SET_LIMIT. Photos that failed to index
    are left out (there's nothing reliable to send)."""
    if selection.kind == SELECTION_ALBUM:
        album = session.get(Album, selection.album_id)
        if album is None:
            raise SelectionMissing(f"album {selection.album_id} no longer exists")
        rows = (session.query(AlbumItem.media_item_id)
                .join(MediaItem, MediaItem.id == AlbumItem.media_item_id)
                .filter(AlbumItem.album_id == album.id, MediaItem.status != MEDIA_STATUS_FAILED)
                .order_by(AlbumItem.position, AlbumItem.media_item_id)
                .limit(DESIRED_SET_LIMIT)
                .all())
        return [Desired(media_item_id=row[0], target=album.name) for row in rows]
    if selection.kind == SELECTION_QUERY:
        problem = validate_query(selection.query or {})
        if problem:
            raise ValueError(problem)
        query = apply_media_filters(session, session.query(MediaItem.id), selections_from_wire(selection.query))
        rows = (query.filter(MediaItem.status != MEDIA_STATUS_FAILED)
                .order_by(MediaItem.id).limit(DESIRED_SET_LIMIT).all())
        return [Desired(media_item_id=row[0], target=default_target) for row in rows]
    if selection.kind == SELECTION_SCRIPT:
        if not script:
            raise SelectionMissing("publish a custom rule before running this sync")
        result = run_starlark(script, inputs={"ctx": {}},
                              functions=build_host_functions(session, include_mutating=False),
                              filename="sync-selector.star")
        if not result.success:
            raise ValueError(f"custom rule failed: {result.error}")
        if not isinstance(result.value, list) or len(result.value) > DESIRED_SET_LIMIT:
            raise ValueError(f"custom rule must return at most {DESIRED_SET_LIMIT} items")
        desired = []
        for item in result.value:
            if not isinstance(item, dict) or set(item) - {"media_item_id", "target", "caption"}:
                raise ValueError("custom rule items must have media_item_id, target, and optional caption")
            media_id, target, caption = item.get("media_item_id"), item.get("target"), item.get("caption")
            if not isinstance(media_id, int) or isinstance(media_id, bool) or not isinstance(target, str) \
                    or not target.strip() or len(target) > 255 or caption is not None \
                    and (not isinstance(caption, str) or len(caption) > 1000):
                raise ValueError("custom rule returned an invalid photo, target, or caption")
            desired.append(Desired(media_id, target.strip(), caption))
        existing = {row[0] for row in session.query(MediaItem.id).filter(
            MediaItem.id.in_([d.media_item_id for d in desired]), MediaItem.status != MEDIA_STATUS_FAILED).all()}
        if any(d.media_item_id not in existing for d in desired):
            raise ValueError("custom rule selected a photo that no longer exists")
        return desired
    raise SelectionUnsupported(f"selection kind {selection.kind!r} isn't supported yet")


def selection_label(session: Session, selection: Selection | None) -> str | None:
    """What the consent sentence and header call the selection."""
    if selection is None:
        return None
    if selection.kind == SELECTION_ALBUM:
        album = session.get(Album, selection.album_id)
        return album.name if album else None
    return None
