import calendar
import os
from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm import Session
from yaffo.db.models import Face, Photo, Tag, PHOTO_STATUS_INDEXED


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


def get_photo_paths_under_path(session: Session, path: str) -> list[tuple[int, str]]:
    """(id, full_file_path) of indexed photos at `path` (an exact file) or under it
    (a directory), ordered by id — for walking the indexed folder tree."""
    path = path.rstrip("/\\")
    under = f"{path}{os.sep}%"
    rows = (
        session.query(Photo.id, Photo.full_file_path)
        .filter(or_(Photo.full_file_path == path, Photo.full_file_path.like(under)))
        .order_by(Photo.id)
        .all()
    )
    return [(row[0], row[1]) for row in rows]


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


def get_all_photo_paths(session: Session) -> list[str]:
    """Every indexed photo's stored file path (skipping any null), for batch jobs
    like the scheduled duplicate scan."""
    rows = session.query(Photo.full_file_path).filter(Photo.full_file_path.isnot(None)).all()
    return [row[0] for row in rows]


def get_paths_by_ids(session: Session, photo_ids: list[int]) -> dict[int, str]:
    return dict(
        session.query(Photo.id, Photo.full_file_path).filter(Photo.id.in_(photo_ids)).all()
    )


def get_indexed_photo_ids(session: Session) -> list[int]:
    """Ids of every indexed photo, for whole-library backfills (e.g. re-classifying
    all photos after the label vocabulary changes)."""
    rows = (
        session.query(Photo.id)
        .filter(Photo.status == PHOTO_STATUS_INDEXED)
        .order_by(Photo.id)
        .all()
    )
    return [row[0] for row in rows]


def update_photo_path(session: Session, photo_id: int, new_path: str) -> None:
    session.query(Photo).filter_by(id=photo_id).update({"full_file_path": new_path})


def move_photo_path(session: Session, old_path: str, new_path: str) -> bool:
    """Update a photo's stored path in place (old_path -> new_path), preserving its
    row id and its faces/tags (which reference photo_id, not the path). Returns True
    if a photo at old_path existed and was moved, False if there was none."""
    updated = (
        session.query(Photo)
        .filter(Photo.full_file_path == old_path)
        .update({"full_file_path": new_path}, synchronize_session=False)
    )
    if updated:
        session.commit()
    return bool(updated)


def get_photos_with_coords(session: Session, photo_ids: list[int]) -> list[Photo]:
    """The given photos that have both latitude and longitude set."""
    if not photo_ids:
        return []
    return (
        session.query(Photo)
        .filter(Photo.id.in_(photo_ids))
        .filter(Photo.latitude.isnot(None))
        .filter(Photo.longitude.isnot(None))
        .all()
    )


def get_named_coordinates(session: Session) -> list[tuple[float, float, str]]:
    """(latitude, longitude, location_name) for every photo that has all three —
    the candidates the assign-location-name automation reuses names from."""
    rows = (
        session.query(Photo.latitude, Photo.longitude, Photo.location_name)
        .filter(Photo.latitude.isnot(None))
        .filter(Photo.longitude.isnot(None))
        .filter(Photo.location_name.isnot(None))
        .filter(Photo.location_name != "")
        .all()
    )
    return [(row[0], row[1], row[2]) for row in rows]


def get_photos_missing_gps(session: Session, photo_ids: list[int]) -> list[Photo]:
    """The given photos that have a capture date but no coordinates — the targets the
    geotag-from-neighbors automation tries to locate."""
    if not photo_ids:
        return []
    return (
        session.query(Photo)
        .filter(Photo.id.in_(photo_ids))
        .filter(Photo.date_taken.isnot(None))
        .filter(Photo.latitude.is_(None))
        .all()
    )


def get_gps_timestamps(session: Session) -> list[tuple[str, float, float, str | None]]:
    """(date_taken, latitude, longitude, location_name) for every photo that has a
    date + coordinates — the GPS-tagged photos the geotag-from-neighbors automation
    borrows coordinates (and, when present, the location name) from."""
    rows = (
        session.query(Photo.date_taken, Photo.latitude, Photo.longitude, Photo.location_name)
        .filter(Photo.date_taken.isnot(None))
        .filter(Photo.latitude.isnot(None))
        .filter(Photo.longitude.isnot(None))
        .all()
    )
    return [(row[0], row[1], row[2], row[3]) for row in rows]


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