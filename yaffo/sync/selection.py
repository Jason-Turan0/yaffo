"""A target's selection -> the desired set: [{media_item_id, target, caption}].

`album`: the album's members, routed to the album's name. `query`: the photos
matching filter-panel selections, stored in the sharing wire format (validated,
distances in km, only the filters that are set), routed to the sync's name.
Selector scripts (`script`) arrive in Phase 3.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from yaffo.db.models import Album, AlbumItem, MediaItem, MEDIA_STATUS_FAILED
from yaffo.db.repositories.media_filter_repository import apply_media_filters
from yaffo.domain.media_filter_params import selections_from_wire, validate_wire_filters
from yaffo.sync.schemas import SELECTION_ALBUM, SELECTION_QUERY, Selection

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


def resolve_selection(session: Session, selection: Selection, default_target: str) -> list[Desired]:
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
    raise SelectionUnsupported(f"selection kind {selection.kind!r} isn't supported yet")


def selection_label(session: Session, selection: Selection | None) -> str | None:
    """What the consent sentence and header call the selection."""
    if selection is None:
        return None
    if selection.kind == SELECTION_ALBUM:
        album = session.get(Album, selection.album_id)
        return album.name if album else None
    return None
