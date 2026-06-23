import calendar
import os
from pathlib import Path

from sqlalchemy import insert, or_
from sqlalchemy.orm import Session
from yaffo.db.models import Face, PersonFace, MediaItem, MediaLabel, Tag, MEDIA_STATUS_INDEXED

# Ids per IN-clause — stay under SQLite's ~999 bound-param cap.
_DELETE_CHUNK = 500


def get_faces_for_media_item(session: Session, media_item_id: int) -> list[Face]:
    return session.query(Face).filter_by(media_item_id=media_item_id).all()


def get_media_item_ids_for_faces(session: Session, face_ids: list[int]) -> list[int]:
    """Distinct ids of the photos those faces belong to — used to announce
    photo_modified for a face-assign without the caller tracking each face's photo."""
    media_item_ids: set[int] = set()
    for start in range(0, len(face_ids), _DELETE_CHUNK):
        chunk = face_ids[start:start + _DELETE_CHUNK]
        media_item_ids.update(
            row[0]
            for row in session.query(Face.media_item_id).filter(
                Face.id.in_(chunk), Face.media_item_id.isnot(None)
            ).all()
        )
    return list(media_item_ids)


def get_media_item_ids_under_path(session: Session, path: str) -> list[int]:
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


def get_media_item_paths_under_path(session: Session, path: str) -> list[tuple[int, str]]:
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


def get_media_item_filename(session: Session, media_item_id: int) -> str | None:
    """The photo's file name (basename of its stored path), for display."""
    path = get_media_item_path(session, media_item_id)
    return Path(path).name if path else None


def get_media_item_filename_for_face(session: Session, face_id: int) -> str | None:
    """The file name of the photo a face belongs to, for display."""
    face = session.get(Face, face_id)
    if face is None or face.media_item_id is None:
        return None
    return get_media_item_filename(session, face.media_item_id)


def add_tag(session: Session, media_item_id: int, name: str, value=None) -> Tag:
    """Tag a photo. `value` is an optional tag value (blank stored as NULL)."""
    tag = Tag(media_item_id=media_item_id, tag_name=name, tag_value=str(value) if value else None)
    session.add(tag)
    session.commit()
    return tag


def add_tags(session: Session, items: list[tuple[int, str, object]]) -> int:
    """Add many tags in one transaction (one executemany insert + commit). `items` is
    [(media_item_id, name, value), ...]; a blank value is stored as NULL. Returns the count
    written. Lets a batch job collect its tags and persist them in a single short write
    rather than committing per photo."""
    rows = [
        {"media_item_id": media_item_id, "tag_name": name, "tag_value": str(value) if value else None}
        for media_item_id, name, value in items
    ]
    if rows:
        session.execute(insert(Tag), rows)
    session.commit()
    return len(rows)


def get_media_item_path(session: Session, media_item_id: int) -> str | None:
    row = session.query(MediaItem.full_file_path).filter_by(id=media_item_id).first()
    return row[0] if row else None


def get_all_media_item_paths(session: Session) -> list[str]:
    """Every indexed photo's stored file path (skipping any null), for batch jobs
    like the scheduled duplicate scan."""
    rows = session.query(MediaItem.full_file_path).filter(MediaItem.full_file_path.isnot(None)).all()
    return [row[0] for row in rows]


def get_paths_by_ids(session: Session, media_item_ids: list[int]) -> dict[int, str]:
    return dict(
        session.query(MediaItem.id, MediaItem.full_file_path).filter(MediaItem.id.in_(media_item_ids)).all()
    )


def get_label_inputs_by_ids(
    session: Session, media_item_ids: list[int]
) -> dict[int, tuple[str, str, float | None]]:
    """For classification: (full_file_path, media_type, duration_seconds) per id. A
    video is labeled from sampled frames rather than its (un-openable) container, so
    the labeler needs the type and duration, not just the path."""
    rows = session.query(
        MediaItem.id, MediaItem.full_file_path, MediaItem.media_type, MediaItem.duration_seconds
    ).filter(MediaItem.id.in_(media_item_ids)).all()
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


def delete_media_items(session: Session, media_item_ids: list[int]) -> list[str]:
    """Remove photos from the index in one transaction: their tags, labels, faces
    (and the people_face links + the face thumbnail files), then the photo rows.
    SQLite FK cascade is off, so dependents are deleted explicitly. Returns the face
    thumbnail paths to unlink (the caller owns the filesystem). Commits."""
    ids = [int(pid) for pid in media_item_ids]
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


def get_indexed_media_item_ids(session: Session) -> list[int]:
    """Ids of every indexed photo, for whole-library backfills (e.g. re-classifying
    all photos after the label vocabulary changes)."""
    rows = (
        session.query(MediaItem.id)
        .filter(MediaItem.status == MEDIA_STATUS_INDEXED)
        .order_by(MediaItem.id)
        .all()
    )
    return [row[0] for row in rows]


def update_media_item_path(session: Session, media_item_id: int, new_path: str) -> None:
    session.query(MediaItem).filter_by(id=media_item_id).update({"full_file_path": new_path})


def move_media_item_path(session: Session, old_path: str, new_path: str) -> bool:
    """Update a photo's stored path in place (old_path -> new_path), preserving its
    row id and its faces/tags (which reference media_item_id, not the path). Returns True
    if a photo at old_path existed and was moved, False if there was none."""
    updated = (
        session.query(MediaItem)
        .filter(MediaItem.full_file_path == old_path)
        .update({"full_file_path": new_path}, synchronize_session=False)
    )
    if updated:
        session.commit()
    return bool(updated)


def get_media_items_with_coords(session: Session, media_item_ids: list[int]) -> list[MediaItem]:
    """The given photos that have both latitude and longitude set."""
    if not media_item_ids:
        return []
    return (
        session.query(MediaItem)
        .filter(MediaItem.id.in_(media_item_ids))
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


def get_media_items_missing_gps(session: Session, media_item_ids: list[int]) -> list[MediaItem]:
    """The given photos that have a capture date but no coordinates — the targets the
    geotag-from-neighbors automation tries to locate."""
    if not media_item_ids:
        return []
    return (
        session.query(MediaItem)
        .filter(MediaItem.id.in_(media_item_ids))
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