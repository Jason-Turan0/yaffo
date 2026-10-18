"""Repair dependents left behind by bulk deletes with SQLite cascades off."""
from sqlalchemy import exists, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from yaffo.db.models import Face, MediaItem, MediaLabel, PersonFace, Tag


def orphaned_person_link() -> ColumnElement[bool]:
    """A person link whose face or that face's media item no longer exists."""
    return ~exists(
        select(Face.id).join(MediaItem, MediaItem.id == Face.media_item_id)
        .where(Face.id == PersonFace.face_id)
    )


def sweep_orphaned_media_dependents(session: Session) -> dict[str, int]:
    """Delete dangling links, labels and tags. The caller owns the transaction."""
    counts = {"person_links": session.query(PersonFace).filter(orphaned_person_link()).delete(
        synchronize_session=False
    )}
    for name, model in (("labels", MediaLabel), ("tags", Tag)):
        counts[name] = session.query(model).filter(
            ~exists().where(MediaItem.id == model.media_item_id)
        ).delete(synchronize_session=False)
    return counts
