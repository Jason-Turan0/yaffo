"""Batch metadata edits and before-state snapshots for sandbox change plans."""
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from yaffo.db.models import (
    Album, AlbumItem, Face, MediaItem, PersonFace, Tag,
    FACE_STATUS_ASSIGNED, FACE_STATUS_UNASSIGNED,
)


VALUE_FIELDS = {"favorite": "favorite", "date": "date_taken", "location_name": "location_name"}


def tag_entries(session: Session, entries: list[dict], *, existing: bool) -> list[dict]:
    result = []
    seen = set()
    for entry in entries:
        key = (entry["media_item_id"], entry["name"], str(entry["value"]) if entry.get("value") else None)
        if key in seen:
            continue
        seen.add(key)
        found = session.query(Tag).filter_by(media_item_id=key[0], tag_name=key[1], tag_value=key[2]).first()
        if (found is not None) == existing:
            result.append({"media_item_id": key[0], "name": key[1], "value": key[2]})
    return result


def remove_tags(session: Session, entries: list[dict]) -> list[int]:
    changed = []
    for entry in tag_entries(session, entries, existing=True):
        removed = session.query(Tag).filter_by(
            media_item_id=entry["media_item_id"], tag_name=entry["name"], tag_value=entry["value"]
        ).delete(synchronize_session="fetch")
        if removed:
            changed.append(entry["media_item_id"])
    session.commit()
    return list(dict.fromkeys(changed))


def face_entries(session: Session, entries: list[dict], *, assigned: bool) -> list[dict]:
    result = []
    seen = set()
    for entry in entries:
        face_id = entry["face_id"]
        if face_id in seen:
            continue
        seen.add(face_id)
        face = session.get(Face, face_id)
        link = session.get(PersonFace, face_id)
        if face is None:
            continue
        if assigned:
            if link and face.status == FACE_STATUS_ASSIGNED and (
                "person_id" not in entry or entry["person_id"] == link.person_id
            ):
                result.append({"face_id": face_id, "person_id": link.person_id, "similarity": link.similarity})
        elif link is None and face.status == FACE_STATUS_UNASSIGNED:
            result.append(dict(entry))
    return result


def unassign_faces(session: Session, entries: list[dict]) -> list[int]:
    changed = []
    for entry in face_entries(session, entries, assigned=True):
        face = session.get(Face, entry["face_id"])
        session.delete(session.get(PersonFace, face.id))
        face.status = FACE_STATUS_UNASSIGNED
        changed.append(face.media_item_id)
    session.commit()
    return list(dict.fromkeys(changed))


def validated_values(entries: list[dict], field: str) -> list[dict]:
    if field not in VALUE_FIELDS:
        raise ValueError("Unknown editable field")
    result = []
    seen = set()
    for entry in entries:
        item_id = entry["id"]
        if type(item_id) is not int or item_id < 1 or item_id in seen:
            raise ValueError("Each media id must be a unique positive integer")
        seen.add(item_id)
        value = entry[field]
        if field == "favorite" and value is not None and type(value) is not bool:
            raise ValueError("favorite must be true, false, or null")
        if field in {"date", "location_name"} and value is not None and not isinstance(value, str):
            raise ValueError(f"{field} must be a string or null")
        if field == "date" and value is not None:
            date = datetime.fromisoformat(value)
            if date.tzinfo is not None:
                raise ValueError("Capture dates must be camera-local, without a timezone")
        result.append(dict(entry))
    return result


def previous_values(session: Session, entries: list[dict], field: str) -> list[dict]:
    result = []
    for entry in validated_values(entries, field):
        item = session.get(MediaItem, entry["id"])
        if item is None:
            continue
        previous = getattr(item, VALUE_FIELDS[field])
        if "expected" in entry and previous != entry["expected"]:
            continue
        if previous != entry[field]:
            inverse = {"id": item.id, field: previous, "expected": entry[field]}
            if field == "date":
                inverse.update(year=item.year, month=item.month)
            result.append(inverse)
    return result


def set_values(session: Session, entries: list[dict], field: str) -> list[int]:
    entries = validated_values(entries, field)
    changed = []
    for entry in entries:
        item = session.get(MediaItem, entry["id"])
        if item is None:
            continue
        old = getattr(item, VALUE_FIELDS[field])
        if "expected" in entry and old != entry["expected"]:
            continue
        if old == entry[field]:
            continue
        setattr(item, VALUE_FIELDS[field], entry[field])
        if field == "date":
            date = datetime.fromisoformat(entry[field]) if entry[field] else None
            item.year = date.year if date else entry.get("year")
            item.month = date.month if date else entry.get("month")
        changed.append(item.id)
    session.commit()
    return changed


def album_members(session: Session, album_id: int, ids: list[int]) -> dict[int, int]:
    return dict(session.query(AlbumItem.media_item_id, AlbumItem.position).filter(
        AlbumItem.album_id == album_id, AlbumItem.media_item_id.in_(ids)).all())


def album_state(session: Session, album_id: int) -> dict[str, Any] | None:
    album = session.get(Album, album_id)
    if album is None:
        return None
    return {"name": album.name, "description": album.description}


def album_is_unchanged(session: Session, album_id: int, expected: dict) -> bool:
    if album_state(session, album_id) != {key: expected[key] for key in ("name", "description")}:
        return False
    if expected.get("empty") and session.query(AlbumItem).filter_by(album_id=album_id).first():
        return False
    return True


def new_album_positions(session: Session, album_id: int, ids: list[int]) -> dict[int, int]:
    members = dict(session.query(AlbumItem.media_item_id, AlbumItem.position).filter_by(album_id=album_id))
    known = {item_id for (item_id,) in session.query(MediaItem.id).filter(MediaItem.id.in_(ids))}
    added = [item_id for item_id in dict.fromkeys(ids) if item_id in known and item_id not in members]
    start = max(members.values(), default=-1) + 1
    return {item_id: start + offset for offset, item_id in enumerate(added)}
