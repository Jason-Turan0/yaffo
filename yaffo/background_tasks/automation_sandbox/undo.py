"""Capture inverse calls immediately before a mutation executes.

No writes happen here. `$result` in an inverse is the just-executed call's return
value; the replay layer must replace it before storing/executing that inverse.
"""
from typing import Any

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.host_types import HostCall
from yaffo.db.repositories import album_repository, person_repository
from yaffo.db.repositories import sandbox_edit_repository as edits


def _batch(name: str, entries: list[dict]) -> list[HostCall]:
    return [HostCall(name, [entries])] if entries else []


def tag_media_items(args: list[Any], session: Session) -> list[HostCall]:
    return _batch("untag_media_items", edits.tag_entries(session, args[0], existing=False))


def untag_media_items(args: list[Any], session: Session) -> list[HostCall]:
    return _batch("tag_media_items", edits.tag_entries(session, args[0], existing=True))


def assign_faces(args: list[Any], session: Session) -> list[HostCall]:
    return _batch("unassign_faces", edits.face_entries(session, args[0], assigned=False))


def unassign_faces(args: list[Any], session: Session) -> list[HostCall]:
    return _batch("assign_faces", edits.face_entries(session, args[0], assigned=True))


def ignore_faces(args: list[Any], session: Session) -> list[HostCall]:
    changed = edits.faces_in_status(session, args[0], edits.IGNORE[0])
    return [HostCall("unignore_faces", [changed])] if changed else []


def unignore_faces(args: list[Any], session: Session) -> list[HostCall]:
    changed = edits.faces_in_status(session, args[0], edits.UNIGNORE[0])
    return [HostCall("ignore_faces", [changed])] if changed else []


def set_favorites(args: list[Any], session: Session) -> list[HostCall]:
    return _batch("set_favorites", edits.previous_values(session, args[0], "favorite"))


def set_media_dates(args: list[Any], session: Session) -> list[HostCall]:
    return _batch("set_media_dates", edits.previous_values(session, args[0], "date"))


def set_location_names(args: list[Any], session: Session) -> list[HostCall]:
    return _batch("set_location_names", edits.previous_values(session, args[0], "location_name"))


def album_exists(args: list[Any], session: Session) -> str | None:
    return None if album_repository.get_album(session, args[0]) else "Album no longer exists"


def create_album(args: list[Any], session: Session) -> list[HostCall]:
    if album_repository.get_album_by_name(session, args[0].strip()) is not None:
        return []
    expected = {"name": args[0].strip(), "description": (args[1] or "").strip() or None if len(args) > 1 else None,
                "empty": True}
    return [HostCall("delete_album", ["$result", expected])]


def update_album(args: list[Any], session: Session) -> list[HostCall]:
    previous = edits.album_state(session, args[0])
    if previous is None:
        return []
    expected = {"name": args[1].strip(), "description": (args[2] or "").strip() or None if len(args) > 2 else None}
    return [HostCall("update_album", [args[0], previous["name"], previous["description"], expected])]


def add_to_album(args: list[Any], session: Session) -> list[HostCall]:
    added = edits.new_album_positions(session, args[0], args[1])
    if len(args) > 2 and args[2] is not None:
        requested = dict(zip(args[1], args[2]))
        added = {item: requested[item] for item in added}
    expected = {str(item): position for item, position in added.items()}
    return [HostCall("remove_from_album", [args[0], list(added), expected])] if added else []


def remove_from_album(args: list[Any], session: Session) -> list[HostCall]:
    existing = edits.album_members(session, args[0], args[1])
    if len(args) > 2 and args[2] is not None:
        existing = {item: pos for item, pos in existing.items() if args[2].get(str(item)) == pos}
    ids = list(existing)
    album = album_repository.get_album(session, args[0])
    cover = album.cover_media_item_id if album and album.cover_media_item_id in ids else None
    return [HostCall("add_to_album", [args[0], ids, [existing[item] for item in ids], cover])] if ids else []


def set_album_cover(args: list[Any], session: Session) -> list[HostCall]:
    previous = edits.album_cover(session, args[0])
    if previous == args[1]:
        return []
    return [HostCall("set_album_cover", [args[0], previous, {"cover": args[1]}])]


def reorder_album(args: list[Any], session: Session) -> list[HostCall]:
    previous = edits.album_order(session, args[0])
    new = edits.full_album_order(session, args[0], args[1])
    if previous == new:
        return []
    return [HostCall("reorder_album", [args[0], previous, {"order": new}])]


def resolve_undo_result(calls: list[HostCall], result: Any) -> list[HostCall]:
    """Bind the successful mutation's return into its precomputed inverses."""
    def replace(value: Any) -> Any:
        if value == "$result":
            return result
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value
    return [HostCall(call.name, replace(call.args)) for call in calls]


def person_exists(args: list[Any], session: Session) -> str | None:
    return None if person_repository.get_person_by_id(session, args[0]) else "Person no longer exists"


def people_exist(args: list[Any], session: Session) -> str | None:
    if args[0] == args[1]:
        return "Can't merge a person into themselves"
    missing = [pid for pid in args[:2] if person_repository.get_person_by_id(session, pid) is None]
    return "Person no longer exists" if missing else None


def create_person(args: list[Any], session: Session) -> list[HostCall]:
    name = (args[0] or "").strip()
    if person_repository.get_person_by_name(session, name) is not None:
        return []
    return [HostCall("delete_person", ["$result", {"name": name, "empty": True}])]


def rename_person(args: list[Any], session: Session) -> list[HostCall]:
    person = person_repository.get_person_by_id(session, args[0])
    if person is None or person.name == (args[1] or "").strip():
        return []
    return [HostCall("rename_person", [args[0], person.name, (args[1] or "").strip()])]
