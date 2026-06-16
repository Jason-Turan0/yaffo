import calendar

from sqlalchemy.orm import Session
from yaffo.db.models import Face, Photo, Tag


def get_faces_for_photo(session: Session, photo_id: int) -> list[Face]:
    return session.query(Face).filter_by(photo_id=photo_id).all()


def add_tag(session: Session, photo_id: int, name: str, value=None) -> Tag:
    """Tag a photo. `value` is an optional tag value (blank stored as NULL)."""
    tag = Tag(photo_id=photo_id, tag_name=name, tag_value=str(value) if value else None)
    session.add(tag)
    session.commit()
    return tag


def get_photo_path(session: Session, photo_id: int) -> str | None:
    row = session.query(Photo.full_file_path).filter_by(id=photo_id).first()
    return row[0] if row else None


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