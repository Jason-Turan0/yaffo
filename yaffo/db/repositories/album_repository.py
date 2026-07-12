"""Albums: curated collections of media items (see docs/development/p2p-sharing.md,
Phase 7).

Membership is explicit — an album is what the user put in it, never a saved
filter. The bulk operations (`add_matching`, `remove_all`) are single SQL
statements rather than loops over ids: adding a filtered selection of 500 photos
must be one INSERT ... SELECT, because the UI's "select all N matching these
filters" posts the *filter*, not an enumeration of ids.
"""
from typing import Optional

from sqlalchemy import and_, delete, func, literal, select
from sqlalchemy.orm import Session

from yaffo.db.models import Album, AlbumItem, MediaItem
from yaffo.db.repositories.media_filter_repository import apply_media_filters
from yaffo.utils.time import utcnow


def list_albums(session: Session) -> list[Album]:
    return session.query(Album).order_by(Album.name).all()


def get_album(session: Session, album_id: int) -> Optional[Album]:
    return session.get(Album, album_id)


def get_album_by_name(session: Session, name: str) -> Optional[Album]:
    return session.query(Album).filter(Album.name == name).one_or_none()


def create_album(session: Session, name: str, description: Optional[str] = None) -> Album:
    name = (name or "").strip()
    if not name:
        raise ValueError("an album needs a name")
    if get_album_by_name(session, name) is not None:
        raise ValueError(f"an album named {name!r} already exists")
    album = Album(name=name, description=(description or "").strip() or None)
    session.add(album)
    session.commit()
    return album


def update_album(
    session: Session, album_id: int, name: str, description: Optional[str] = None
) -> Album:
    album = _require_album(session, album_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("an album needs a name")
    clash = get_album_by_name(session, name)
    if clash is not None and clash.id != album.id:
        raise ValueError(f"an album named {name!r} already exists")
    album.name = name
    album.description = (description or "").strip() or None
    session.commit()
    return album


def delete_album(session: Session, album_id: int) -> bool:
    """Delete the album and its membership rows. Never touches the media items —
    the photos and their files are untouched."""
    album = session.get(Album, album_id)
    if album is None:
        return False
    session.delete(album)  # album_items cascade
    session.commit()
    return True


def set_cover(session: Session, album_id: int, media_item_id: int) -> Album:
    """Pin the album's cover. The item must be a member — a cover pointing outside
    the album would show a photo the album does not contain."""
    album = _require_album(session, album_id)
    if not _is_member(session, album_id, media_item_id):
        raise ValueError("the cover must be a member of the album")
    album.cover_media_item_id = media_item_id
    session.commit()
    return album


# ---- membership -------------------------------------------------------------

def list_items(session: Session, album_id: int) -> list[MediaItem]:
    """The album's media items in manual order."""
    return (
        session.query(MediaItem)
        .join(AlbumItem, AlbumItem.media_item_id == MediaItem.id)
        .filter(AlbumItem.album_id == album_id)
        .order_by(AlbumItem.position, AlbumItem.added_at)
        .all()
    )


def item_count(session: Session, album_id: int) -> int:
    return (
        session.query(func.count(AlbumItem.media_item_id))
        .filter(AlbumItem.album_id == album_id)
        .scalar()
        or 0
    )


def item_counts(session: Session) -> dict[int, int]:
    """Member count per album id — one query, for the sidebar and overview tiles."""
    rows = (
        session.query(AlbumItem.album_id, func.count(AlbumItem.media_item_id))
        .group_by(AlbumItem.album_id)
        .all()
    )
    return {album_id: count for album_id, count in rows}


def cover_media_item(session: Session, album: Album) -> Optional[MediaItem]:
    """The album's cover, falling back to its first member when none is pinned.
    None only when the album is empty (the UI then shows the theme placeholder).

    The whole item, not just its id: a video cover has to be drawn from its poster
    (its original file is a video, which an <img> cannot render), so the caller needs
    the media type and poster too."""
    if album.cover_media_item_id is not None:
        cover = session.get(MediaItem, album.cover_media_item_id)
        if cover is not None:
            return cover
    return (
        session.query(MediaItem)
        .join(AlbumItem, AlbumItem.media_item_id == MediaItem.id)
        .filter(AlbumItem.album_id == album.id)
        .order_by(AlbumItem.position, AlbumItem.added_at)
        .first()
    )


def exclude_members(query, album_id: int):
    """Narrow a MediaItem query to photos NOT already in the album — what the add
    screen shows, so you only ever look at photos you could actually add."""
    members = select(AlbumItem.media_item_id).where(AlbumItem.album_id == album_id)
    return query.filter(MediaItem.id.not_in(members))


def add_items(session: Session, album_id: int, media_item_ids: list[int]) -> int:
    """Add the given items, skipping any already in the album. Returns how many
    were actually added."""
    _require_album(session, album_id)
    if not media_item_ids:
        return 0
    existing = {
        row[0]
        for row in session.query(AlbumItem.media_item_id)
        .filter(AlbumItem.album_id == album_id, AlbumItem.media_item_id.in_(media_item_ids))
        .all()
    }
    # Ignore ids that are not media items at all rather than failing the whole add.
    known = {
        row[0]
        for row in session.query(MediaItem.id).filter(MediaItem.id.in_(media_item_ids)).all()
    }
    to_add = [item_id for item_id in media_item_ids if item_id in known and item_id not in existing]
    if not to_add:
        return 0
    position = _next_position(session, album_id)
    for offset, media_item_id in enumerate(to_add):
        session.add(
            AlbumItem(album_id=album_id, media_item_id=media_item_id, position=position + offset)
        )
    session.commit()
    return len(to_add)


def add_matching(
    session: Session, album_id: int, filters: dict, exclude_ids: Optional[list[int]] = None
) -> int:
    """Add every media item matching `filters` (the home page's filter selections,
    as understood by apply_media_filters) in ONE statement — this is what backs
    the add screen's "select all N matching these filters". Items already in the
    album are skipped (the composite PK makes that an OR IGNORE, not a lookup).
    Returns how many rows were inserted."""
    _require_album(session, album_id)
    matching = apply_media_filters(session, session.query(MediaItem), filters)
    # Existing members are excluded here rather than left to the OR IGNORE below, so
    # the positions row_number() hands out stay contiguous (an ignored row would
    # still have burned its number) and the returned count is the number added.
    matching = exclude_members(matching, album_id)
    if exclude_ids:
        # "Everything matching, EXCEPT these" — the photos the user unticked while
        # the whole scope was selected.
        matching = matching.filter(MediaItem.id.not_in(exclude_ids))
    # Newest first, matching how the grid presents them, so manual order starts out
    # the same as what the user was looking at when they hit Add.
    numbered = matching.with_entities(
        literal(album_id),
        MediaItem.id,
        _next_position(session, album_id)
        - 1
        + func.row_number().over(order_by=MediaItem.date_taken.desc()),
        literal(utcnow()),
    ).statement

    insert = (
        AlbumItem.__table__.insert()
        .prefix_with("OR IGNORE")  # already-a-member is a no-op, not a failure
        .from_select(["album_id", "media_item_id", "position", "added_at"], numbered)
    )
    added = session.execute(insert).rowcount
    session.commit()
    return added


def remove_items(session: Session, album_id: int, media_item_ids: list[int]) -> int:
    """Remove members. Only the album_items rows go — never the photos."""
    if not media_item_ids:
        return 0
    removed = session.execute(
        delete(AlbumItem).where(
            AlbumItem.album_id == album_id, AlbumItem.media_item_id.in_(media_item_ids)
        )
    ).rowcount
    _clear_dangling_cover(session, album_id)
    session.commit()
    return removed


def remove_all(
    session: Session, album_id: int, exclude_ids: Optional[list[int]] = None
) -> int:
    """Remove every member in one statement — what "select all" + Remove posts —
    keeping any the user unticked out of the scope (`exclude_ids`)."""
    condition = AlbumItem.album_id == album_id
    if exclude_ids:
        condition = and_(condition, AlbumItem.media_item_id.not_in(exclude_ids))
    removed = session.execute(delete(AlbumItem).where(condition)).rowcount
    _clear_dangling_cover(session, album_id)
    session.commit()
    return removed


def reorder(session: Session, album_id: int, ordered_media_item_ids: list[int]) -> None:
    """Persist manual order (drag-to-reorder). Ids not in the album are ignored;
    members missing from the list keep their relative order after the listed ones."""
    members = {
        row[0]
        for row in session.query(AlbumItem.media_item_id)
        .filter(AlbumItem.album_id == album_id)
        .all()
    }
    position = 0
    for media_item_id in ordered_media_item_ids:
        if media_item_id not in members:
            continue
        session.query(AlbumItem).filter(
            AlbumItem.album_id == album_id, AlbumItem.media_item_id == media_item_id
        ).update({AlbumItem.position: position})
        position += 1
    session.commit()


# ---- internals --------------------------------------------------------------

def _require_album(session: Session, album_id: int) -> Album:
    album = session.get(Album, album_id)
    if album is None:
        raise ValueError(f"no album with id {album_id}")
    return album


def _is_member(session: Session, album_id: int, media_item_id: int) -> bool:
    return session.execute(
        select(AlbumItem.media_item_id).where(
            AlbumItem.album_id == album_id, AlbumItem.media_item_id == media_item_id
        )
    ).first() is not None


def _next_position(session: Session, album_id: int) -> int:
    highest = (
        session.query(func.max(AlbumItem.position))
        .filter(AlbumItem.album_id == album_id)
        .scalar()
    )
    return 0 if highest is None else highest + 1


def _clear_dangling_cover(session: Session, album_id: int) -> None:
    """A pinned cover that just left the album would render a photo the album no
    longer contains; drop it back to the first-member fallback."""
    album = session.get(Album, album_id)
    if album is None or album.cover_media_item_id is None:
        return
    if not _is_member(session, album_id, album.cover_media_item_id):
        album.cover_media_item_id = None
