import calendar
import os
from pathlib import Path

from sqlalchemy import insert, or_
from sqlalchemy.orm import Session
from yaffo.db.models import Face, PersonFace, MediaItem, MediaLabel, Tag, PHOTO_STATUS_INDEXED

# Ids per IN-clause — stay under SQLite's ~999 bound-param cap.
_DELETE_CHUNK = 500


def get_faces_for_photo(session: Session, photo_id: int) -> list[Face]:
    return session.query(Face).filter_by(media_item_id=photo_id).all()


def get_photo_ids_for_faces(session: Session, face_ids: list[int]) -> list[int]:
    """Distinct ids of the photos those faces belong to — used to announce
    photo_modified for a face-assign without the caller tracking each face's photo."""
    photo_ids: set[int] = set()
    for start in range(0, len(face_ids), _DELETE_CHUNK):
        chunk = face_ids[start:start + _DELETE_CHUNK]
        photo_ids.update(
            row[0]
            for row in session.query(Face.media_item_id).filter(
                Face.id.in_(chunk), Face.media_item_id.isnot(None)
            ).all()
        )
    return list(photo_ids)


def get_photo_ids_under_path(session: Session, path: str) -> list[int]:
    """Ids of indexed photos at `path` (an exact file) or under it (a directory)."""
    path = path.rstrip("/\\")
    under = f"{path}{os.sep}%"
    rows = (
        session.query(MediaItem.id)
        .filter(or_(MediaItem.full_file_path == path, MediaItem.full_file_path.like(under)))
        .order_by(MediaItem.id)
        .all()
    )
    return [row[0] for row in rows]


def get_photo_paths_under_path(session: Session, path: str) -> list[tuple[int, str]]:
    """(id, full_file_path) of indexed photos at `path` (an exact file) or under it
    (a directory), ordered by id — for walking the indexed folder tree."""
    path = path.rstrip("/\\")
    under = f"{path}{os.sep}%"
    rows = (
        session.query(MediaItem.id, MediaItem.full_file_path)
        .filter(or_(MediaItem.full_file_path == path, MediaItem.full_file_path.like(under)))
        .order_by(MediaItem.id)
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
    if face is None or face.media_item_id is None:
        return None
    return get_photo_filename(session, face.media_item_id)


def add_tag(session: Session, photo_id: int, name: str, value=None) -> Tag:
    """Tag a photo. `value` is an optional tag value (blank stored as NULL)."""
    tag = Tag(media_item_id=photo_id, tag_name=name, tag_value=str(value) if value else None)
    session.add(tag)
    session.commit()
    return tag


def add_tags(session: Session, items: list[tuple[int, str, object]]) -> int:
    """Add many tags in one transaction (one executemany insert + commit). `items` is
    [(photo_id, name, value), ...]; a blank value is stored as NULL. Returns the count
    written. Lets a batch job collect its tags and persist them in a single short write
    rather than committing per photo."""
    rows = [
        {"media_item_id": photo_id, "tag_name": name, "tag_value": str(value) if value else None}
        for photo_id, name, value in items
    ]
    if rows:
        session.execute(insert(Tag), rows)
    session.commit()
    return len(rows)


def get_photo_path(session: Session, photo_id: int) -> str | None:
    row = session.query(MediaItem.full_file_path).filter_by(id=photo_id).first()
    return row[0] if row else None


def get_all_photo_paths(session: Session) -> list[str]:
    """Every indexed photo's stored file path (skipping any null), for batch jobs
    like the scheduled duplicate scan."""
    rows = session.query(MediaItem.full_file_path).filter(MediaItem.full_file_path.isnot(None)).all()
    return [row[0] for row in rows]


def get_paths_by_ids(session: Session, photo_ids: list[int]) -> dict[int, str]:
    return dict(
        session.query(MediaItem.id, MediaItem.full_file_path).filter(MediaItem.id.in_(photo_ids)).all()
    )


def delete_photos(session: Session, photo_ids: list[int]) -> list[str]:
    """Remove photos from the index in one transaction: their tags, labels, faces
    (and the people_face links + the face thumbnail files), then the photo rows.
    SQLite FK cascade is off, so dependents are deleted explicitly. Returns the face
    thumbnail paths to unlink (the caller owns the filesystem). Commits."""
    ids = [int(pid) for pid in photo_ids]
    if not ids:
        return []
    thumbnails: list[str] = []
    for start in range(0, len(ids), _DELETE_CHUNK):
        chunk = ids[start:start + _DELETE_CHUNK]
        face_rows = (
            session.query(Face.id, Face.full_file_path).filter(Face.media_item_id.in_(chunk)).all()
        )
        face_ids = [row[0] for row in face_rows]
        thumbnails.extend(row[1] for row in face_rows if row[1])
        for f_start in range(0, len(face_ids), _DELETE_CHUNK):
            f_chunk = face_ids[f_start:f_start + _DELETE_CHUNK]
            session.query(PersonFace).filter(PersonFace.face_id.in_(f_chunk)).delete(synchronize_session=False)
        session.query(Face).filter(Face.media_item_id.in_(chunk)).delete(synchronize_session=False)
        session.query(MediaLabel).filter(MediaLabel.media_item_id.in_(chunk)).delete(synchronize_session=False)
        session.query(Tag).filter(Tag.media_item_id.in_(chunk)).delete(synchronize_session=False)
        session.query(MediaItem).filter(MediaItem.id.in_(chunk)).delete(synchronize_session=False)
    session.commit()
    return thumbnails


def get_indexed_photo_ids(session: Session) -> list[int]:
    """Ids of every indexed photo, for whole-library backfills (e.g. re-classifying
    all photos after the label vocabulary changes)."""
    rows = (
        session.query(MediaItem.id)
        .filter(MediaItem.status == PHOTO_STATUS_INDEXED)
        .order_by(MediaItem.id)
        .all()
    )
    return [row[0] for row in rows]


def update_photo_path(session: Session, photo_id: int, new_path: str) -> None:
    session.query(MediaItem).filter_by(id=photo_id).update({"full_file_path": new_path})


def move_photo_path(session: Session, old_path: str, new_path: str) -> bool:
    """Update a photo's stored path in place (old_path -> new_path), preserving its
    row id and its faces/tags (which reference photo_id, not the path). Returns True
    if a photo at old_path existed and was moved, False if there was none."""
    updated = (
        session.query(MediaItem)
        .filter(MediaItem.full_file_path == old_path)
        .update({"full_file_path": new_path}, synchronize_session=False)
    )
    if updated:
        session.commit()
    return bool(updated)


def get_photos_with_coords(session: Session, photo_ids: list[int]) -> list[MediaItem]:
    """The given photos that have both latitude and longitude set."""
    if not photo_ids:
        return []
    return (
        session.query(MediaItem)
        .filter(MediaItem.id.in_(photo_ids))
        .filter(MediaItem.latitude.isnot(None))
        .filter(MediaItem.longitude.isnot(None))
        .all()
    )


def get_named_coordinates(session: Session) -> list[tuple[float, float, str]]:
    """(latitude, longitude, location_name) for every photo that has all three —
    the candidates the assign-location-name automation reuses names from."""
    rows = (
        session.query(MediaItem.latitude, MediaItem.longitude, MediaItem.location_name)
        .filter(MediaItem.latitude.isnot(None))
        .filter(MediaItem.longitude.isnot(None))
        .filter(MediaItem.location_name.isnot(None))
        .filter(MediaItem.location_name != "")
        .all()
    )
    return [(row[0], row[1], row[2]) for row in rows]


def get_photos_missing_gps(session: Session, photo_ids: list[int]) -> list[MediaItem]:
    """The given photos that have a capture date but no coordinates — the targets the
    geotag-from-neighbors automation tries to locate."""
    if not photo_ids:
        return []
    return (
        session.query(MediaItem)
        .filter(MediaItem.id.in_(photo_ids))
        .filter(MediaItem.date_taken.isnot(None))
        .filter(MediaItem.latitude.is_(None))
        .all()
    )


def get_gps_timestamps(session: Session) -> list[tuple[str, float, float, str | None]]:
    """(date_taken, latitude, longitude, location_name) for every photo that has a
    date + coordinates — the GPS-tagged photos the geotag-from-neighbors automation
    borrows coordinates (and, when present, the location name) from."""
    rows = (
        session.query(MediaItem.date_taken, MediaItem.latitude, MediaItem.longitude, MediaItem.location_name)
        .filter(MediaItem.date_taken.isnot(None))
        .filter(MediaItem.latitude.isnot(None))
        .filter(MediaItem.longitude.isnot(None))
        .all()
    )
    return [(row[0], row[1], row[2], row[3]) for row in rows]


def get_distinct_years(session: Session) -> list[int]:
    return [row[0] for row in
            (session
                .query(MediaItem.year)
                .filter(MediaItem.year.isnot(None))
                .distinct()
                .order_by(MediaItem.year)
                .all())
            ]

def get_distinct_months():
    return [
        {'value': i, 'name': calendar.month_name[i]}
        for i in range(1, 13)
    ]