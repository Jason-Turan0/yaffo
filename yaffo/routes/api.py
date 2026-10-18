"""The `/api/` blueprint: library resources that more than one page uses and that
don't depend on any one page's UI (docs/development/web-routes-and-request-security.md).

Paths name resources and the HTTP method says what happens to them. Requests with
a body must be JSON (`415` otherwise), and every error is
`{"error": <translated message>, "code": <stable code>}`. Routes used by one page,
or shaped for its UI, live under that page instead.
"""
from __future__ import annotations

import subprocess

import pydash as py_
import requests
from flask import Blueprint, Response, current_app, jsonify, request
from flask_babel import gettext, ngettext

from yaffo.background_tasks.events import emit_event
from yaffo.background_tasks.tasks.assign_faces_to_person import (
    assign_faces_to_person,
    assign_faces_to_person_now,
)
from yaffo.db import db
from yaffo.db.models import (
    EVENT_MEDIA_MODIFIED,
    FACE_STATUS_ASSIGNED,
    FACE_STATUS_IGNORED,
    FACE_STATUS_PROCESSING,
    FACE_STATUS_UNASSIGNED,
    Face,
    MediaItem,
    Person,
    Tag,
)
from yaffo.db.repositories.person_repository import clear_faces
from yaffo.demo import DEMO_ROLE_RECEIVER, DEMO_ROLE_SOURCE, demo_unsafe_allowed
from yaffo.logging_config import get_logger
from yaffo.site_agents.assistant.file_targets import SHOW_FOLDER, FileTarget, TargetError, resolve_target
from yaffo.utils.open_in_os import open_in_os

logger = get_logger(__name__, "webapp")

api = Blueprint("api", __name__, url_prefix="/api")

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _error(message: str, code: str, status: int) -> tuple[Response, int]:
    return jsonify({"error": message, "code": code}), status


@api.before_request
def require_json_body():
    """JSON in: a second barrier against cross-site form posts, which can't send it."""
    if request.method in _BODY_METHODS and not request.is_json:
        return _error(gettext("Send the request body as JSON."), "json_required", 415)
    return None


def _json_body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@api.route("/media/<int:media_item_id>", methods=["PATCH"])
def media_update(media_item_id: int):
    """Change a photo's fields. Only `favorite` so far: `true` marks it, `false`
    clears it (stored as NULL, so the export and filters treat it as unset). Emits
    media_modified when the value changes, so the export automation can write it."""
    media_item = db.session.get(MediaItem, media_item_id)
    if not media_item:
        return _error(gettext("Photo not found"), "photo_not_found", 404)

    favorite = _json_body().get("favorite")
    if not isinstance(favorite, bool):
        return _error(gettext("Favorite must be true or false"), "favorite_must_be_boolean", 400)

    new_value = True if favorite else None
    if media_item.favorite != new_value:
        media_item.favorite = new_value
        db.session.commit()
        emit_event(EVENT_MEDIA_MODIFIED, {"media_item_ids": [media_item_id]})
    return jsonify({"id": media_item_id, "favorite": bool(media_item.favorite)})


def _face_ids_or_none(raw) -> list[int] | None:
    if not isinstance(raw, list) or not raw:
        return None
    try:
        return list(dict.fromkeys(int(face_id) for face_id in raw))
    except (TypeError, ValueError):
        return None


def _faces_response(face_ids: list[int], status: str, person_id: int | None, message: str, code: str):
    return jsonify({
        "faces": [{"id": face_id, "status": status, "person_id": person_id} for face_id in face_ids],
        "message": message,
        "code": code,
    })


def _available_count(face_ids: list[int]) -> int:
    """How many of the faces are free to assign or ignore: unassigned, or ignored
    (the Faces page can include ignored faces, so a past ignore can be revisited)."""
    return (
        db.session.query(Face.id)
        .filter(Face.id.in_(face_ids), Face.status.in_((FACE_STATUS_UNASSIGNED, FACE_STATUS_IGNORED)))
        .count()
    )


def _assign_faces(face_ids: list[int], raw_person_id):
    try:
        person_id = int(raw_person_id)
    except (TypeError, ValueError):
        return _error(gettext("Person not found"), "person_not_found", 404)
    person = db.session.get(Person, person_id)
    if person is None:
        return _error(gettext("Person not found"), "person_not_found", 404)
    if _available_count(face_ids) != len(face_ids):
        return _error(gettext("One or more faces are no longer available"), "faces_not_available", 409)

    if current_app.config.get("DEMO_MODE"):
        assign_faces_to_person_now(db.session, person_id, face_ids, emit_change_event=False)
        status = FACE_STATUS_ASSIGNED
    else:
        db.session.query(Face).filter(Face.id.in_(face_ids)).update(
            {Face.status: FACE_STATUS_PROCESSING}, synchronize_session=False
        )
        db.session.commit()
        assign_faces_to_person(person_id, face_ids)
        status = FACE_STATUS_PROCESSING
    message = ngettext(
        "Successfully assigned %(count)s face to %(person)s",
        "Successfully assigned %(count)s faces to %(person)s",
        len(face_ids),
        count=len(face_ids),
        person=person.name,
    )
    return _faces_response(face_ids, status, person_id, message, "faces_assigned")


def _unassign_faces(face_ids: list[int]):
    """Back to unassigned: unlink from their person, or undo an ignore."""
    cleared = clear_faces(db.session, face_ids)
    if not cleared.face_ids:
        return _error(gettext("This face isn't assigned to anyone"), "faces_not_assigned", 409)
    if cleared.media_item_ids:
        emit_event(EVENT_MEDIA_MODIFIED, {"media_item_ids": cleared.media_item_ids})
    message = ngettext(
        "Cleared %(count)s face",
        "Cleared %(count)s faces",
        len(cleared.face_ids),
        count=len(cleared.face_ids),
    )
    return _faces_response(cleared.face_ids, FACE_STATUS_UNASSIGNED, None, message, "faces_unassigned")


def _ignore_faces(face_ids: list[int]):
    if _available_count(face_ids) != len(face_ids):
        return _error(gettext("One or more faces are no longer available"), "faces_not_available", 409)
    db.session.query(Face).filter(Face.id.in_(face_ids)).update(
        {Face.status: FACE_STATUS_IGNORED}, synchronize_session=False
    )
    db.session.commit()
    message = ngettext(
        "Successfully ignored %(count)s face",
        "Successfully ignored %(count)s faces",
        len(face_ids),
        count=len(face_ids),
    )
    return _faces_response(face_ids, FACE_STATUS_IGNORED, None, message, "faces_ignored")


@api.route("/faces", methods=["PATCH"])
@demo_unsafe_allowed(DEMO_ROLE_SOURCE, DEMO_ROLE_RECEIVER)
def faces_update():
    """Change the given faces' `person_id` or `status`:

    - `{"ids": [...], "person_id": 7}` assigns unassigned or ignored faces to a person;
    - `{"ids": [...], "person_id": null}` clears assigned or ignored faces;
    - `{"ids": [...], "status": "IGNORED"}` ignores unassigned faces (already-ignored
      ones stay ignored).

    Faces in the wrong state for the change answer `409`."""
    data = _json_body()
    face_ids = _face_ids_or_none(data.get("ids"))
    if face_ids is None:
        return _error(gettext("Faces must contain numeric identifiers"), "invalid_face_ids", 400)

    has_person = "person_id" in data
    status = str(data.get("status") or "").upper()
    if has_person == bool(status) or (status and status != FACE_STATUS_IGNORED):
        return _error(gettext("Faces, person, and face status are required"), "assignment_fields_required", 400)

    try:
        if status:
            return _ignore_faces(face_ids)
        if data["person_id"] is None:
            return _unassign_faces(face_ids)
        return _assign_faces(face_ids, data["person_id"])
    except Exception:
        db.session.rollback()
        logger.exception("Error updating faces")
        return _error(gettext("Could not process faces"), "face_processing_failed", 500)


@api.route("/tags/<path:tag_name>/values", methods=["GET"])
def tag_values_list(tag_name: str):
    """The distinct values a tag has across the library. `path` so a tag name
    containing "/" still matches."""
    values = (
        db.session.query(Tag.tag_value)
        .filter(Tag.tag_name == tag_name)
        .filter(Tag.tag_value.isnot(None))
        .distinct()
        .order_by(Tag.tag_value)
        .all()
    )
    return jsonify({"tag": tag_name, "values": [value for value, in values if value]})


@api.route("/locations", methods=["GET"])
def locations_list():
    """Places matching `q`: named locations from the library's photos, then
    OpenStreetMap Nominatim geocoding results."""
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify({"results": []})

    results = []
    db_locations = (
        db.session.query(MediaItem.location_name, MediaItem.latitude, MediaItem.longitude)
        .filter(MediaItem.location_name.isnot(None))
        .filter(MediaItem.location_name.ilike(f"%{query}%"))
        .limit(5)
        .all()
    )
    for photos_by_name in py_.group_by(db_locations, lambda media_item: media_item.location_name).values():
        lat = py_.sum_by(photos_by_name, lambda media_item: media_item.latitude) / len(photos_by_name)
        lon = py_.sum_by(photos_by_name, lambda media_item: media_item.longitude) / len(photos_by_name)
        if lat is not None and lon is not None:
            results.append({
                "name": photos_by_name[0].location_name,
                "lat": lat,
                "lon": lon,
                "source": "photos",
            })

    try:
        osm_response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 5},
            headers={"User-Agent": "PhotoOrganizer/1.0"},
            timeout=3,
        )
        if osm_response.status_code == 200:
            for item in osm_response.json():
                results.append({
                    "name": item.get("display_name"),
                    "lat": float(item.get("lat")),
                    "lon": float(item.get("lon")),
                    "source": "osm",
                })
    except requests.RequestException:
        pass

    return jsonify({"results": results})


@api.route("/openings", methods=["POST"])
def openings_create():
    """Show a file or folder on this computer. The body is a FileTarget, which
    names it by ids only; the path is looked up here and must be inside a
    configured media folder, so nothing else can be opened. `show: "folder"` on a
    file reveals the file in its folder. `202`: the OS opens it after we answer."""
    try:
        target = FileTarget.from_dict(request.get_json(silent=True))
        resolved = resolve_target(db.session, target)
    except TargetError:  # its reasons are worded for the model; the user gets one message
        return _error(gettext("That file or folder isn't available. It may have moved, or its drive "
                              "isn't connected."), "open_target_unavailable", 404)
    try:
        open_in_os(resolved.path, reveal=target.show == SHOW_FOLDER and not resolved.is_dir)
    except (OSError, subprocess.SubprocessError):
        return _error(gettext("Couldn't open it on this computer."), "open_failed", 500)
    return "", 202


def init_api_routes(app) -> None:
    app.register_blueprint(api)
