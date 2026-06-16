import calendar
import os
from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm import Session
from yaffo.db.models import Face, Photo, Tag


def get_faces_for_photo(session: Session, photo_id: int) -> list[Face]:
    return session.query(Face).filter_by(photo_id=photo_id).all()


def get_photo_ids_under_path(session: Session, path: str) -> list[int]:
    """Ids of indexed photos at `path` (an exact file) or under it (a directory)."""
    path = path.rstrip("/\\")
    under = f"{path}{os.sep}%"
    rows = (
        session.query(Photo.id)
        .filter(or_(Photo.full_file_path == path, Photo.full_file_path.like(under)))
        .order_by(Photo.id)
        .all()
    )
    return [row[0] for row in rows]


def get_photo_filename(session: Session, photo_id: int) -> str | None:
    """The photo's file name (basename of its stored path), for display."""
    path = get_photo_path(session, photo_id)
    return Path(path).name if path else None


def get_photo_filename_for_face(session: Session, face_id: int) -> str | None:
    """The file name of the photo a face belongs to, for display."""
    face = session.get(Face, face_id)
    if face is None or face.photo_id is None:
        return None
    return get_photo_filename(session, face.photo_id)


def add_tag(session: Session, photo_id: int, name: str, value=None) -> Tag:
    """Tag a photo. `value` is an optional tag value (blank stored as NULL)."""
    tag = Tag(photo_id=photo_id, tag_name=name, tag_value=str(value) if value else None)
    session.add(tag)
    session.commit()
    return tag


def get_photo_path(session: Session, photo_id: int) -> str | None:
    row = session.query(Photo.full_file_path).filter_by(id=photo_id).first()
    return row[0] if row else None


def get_paths_by_ids(session: Session, photo_ids: list[int]) -> dict[int, str]:
    return dict(
        session.query(Photo.id, Photo.full_file_path).filter(Photo.id.in_(photo_ids)).all()
    )


def update_photo_path(session: Session, photo_id: int, new_path: str) -> None:
    session.query(Photo).filter_by(id=photo_id).update({"full_file_path": new_path})
    session.commit()


def get_distinct_years(session: Session) -> list[int]:
    return [row[0] for row in
            (session
                .query(Photo.year)
                .filter(Photo.year.isnot(None))
                .distinct()
                .order_by(Photo.year)
                .all())
            ]

def get_distinct_months():
    return [
        {'value': i, 'name': calendar.month_name[i]}
        for i in range(1, 13)
    ]