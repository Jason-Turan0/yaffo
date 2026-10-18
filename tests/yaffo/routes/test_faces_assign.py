"""PATCH /api/faces takes any number of faces in one request.

A 50-face cap (meant for the public demo, but applied everywhere) once made
assigning or ignoring a large cluster fail with "Too many faces were selected".
There is no per-request limit; these tests keep it that way.
"""
import pytest

from yaffo.db import db
from yaffo.db.models import (
    FACE_STATUS_ASSIGNED,
    FACE_STATUS_IGNORED,
    FACE_STATUS_PROCESSING,
    FACE_STATUS_UNASSIGNED,
    Face,
    Person,
)

pytestmark = pytest.mark.unit

# Comfortably past the old 50-face cap.
FACE_COUNT = 500


def _add_unassigned_faces(count: int) -> list[int]:
    faces = [
        Face(full_file_path=f"/faces/{index}.jpg", status=FACE_STATUS_UNASSIGNED)
        for index in range(count)
    ]
    db.session.add_all(faces)
    db.session.commit()
    return [face.id for face in faces]


def test_assigns_every_selected_face_with_no_per_request_limit(app, client, monkeypatch):
    queued = []
    monkeypatch.setattr(
        "yaffo.routes.api.assign_faces_to_person",
        lambda person_id, face_ids: queued.append((person_id, face_ids)),
    )
    with app.app_context():
        face_ids = _add_unassigned_faces(FACE_COUNT)
        person = Person(name="Ada")
        db.session.add(person)
        db.session.commit()
        person_id = person.id

    response = client.patch("/api/faces", json={"ids": face_ids, "person_id": person_id})

    assert response.status_code == 200
    body = response.get_json()
    assert body["code"] == "faces_assigned"
    assert [face["id"] for face in body["faces"]] == face_ids
    assert {(face["status"], face["person_id"]) for face in body["faces"]} == {(FACE_STATUS_PROCESSING, person_id)}
    assert queued == [(person_id, face_ids)]
    with app.app_context():
        statuses = {status for (status,) in db.session.query(Face.status)}
        assert statuses == {FACE_STATUS_PROCESSING}


def test_ignores_every_selected_face_with_no_per_request_limit(app, client):
    with app.app_context():
        face_ids = _add_unassigned_faces(FACE_COUNT)

    response = client.patch("/api/faces", json={"ids": face_ids, "status": FACE_STATUS_IGNORED})

    assert response.status_code == 200
    assert response.get_json()["code"] == "faces_ignored"
    with app.app_context():
        statuses = {status for (status,) in db.session.query(Face.status)}
        assert statuses == {FACE_STATUS_IGNORED}


@pytest.mark.parametrize("faces", ["12", 12, {"1": 1}])
def test_rejects_faces_that_are_not_a_list(client, faces):
    response = client.patch("/api/faces", json={"ids": faces, "status": FACE_STATUS_IGNORED})

    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_face_ids"


def test_assigning_already_assigned_faces_conflicts(app, client):
    with app.app_context():
        face_ids = _add_unassigned_faces(2)
        db.session.get(Face, face_ids[0]).status = FACE_STATUS_ASSIGNED
        person = Person(name="Ada")
        db.session.add(person)
        db.session.commit()
        person_id = person.id

    response = client.patch("/api/faces", json={"ids": face_ids, "person_id": person_id})

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "One or more faces are no longer available",
        "code": "faces_not_available",
    }


def test_ignored_faces_can_be_assigned(app, client, monkeypatch):
    """The Faces page's "Include ignored faces" filter shows ignored faces next to
    unassigned ones, so a mixed selection must assign rather than conflict."""
    queued = []
    monkeypatch.setattr(
        "yaffo.routes.api.assign_faces_to_person",
        lambda person_id, face_ids: queued.append((person_id, face_ids)),
    )
    with app.app_context():
        face_ids = _add_unassigned_faces(2)
        db.session.get(Face, face_ids[0]).status = FACE_STATUS_IGNORED
        person = Person(name="Ada")
        db.session.add(person)
        db.session.commit()
        person_id = person.id

    response = client.patch("/api/faces", json={"ids": face_ids, "person_id": person_id})

    assert response.status_code == 200
    assert queued == [(person_id, face_ids)]
    with app.app_context():
        assert {status for (status,) in db.session.query(Face.status)} == {FACE_STATUS_PROCESSING}


def test_ignoring_already_ignored_faces_succeeds(app, client):
    with app.app_context():
        face_ids = _add_unassigned_faces(2)
        db.session.get(Face, face_ids[0]).status = FACE_STATUS_IGNORED
        db.session.commit()

    response = client.patch("/api/faces", json={"ids": face_ids, "status": FACE_STATUS_IGNORED})

    assert response.status_code == 200
    with app.app_context():
        assert {status for (status,) in db.session.query(Face.status)} == {FACE_STATUS_IGNORED}


def test_ignoring_assigned_faces_conflicts(app, client):
    with app.app_context():
        face_ids = _add_unassigned_faces(1)
        db.session.get(Face, face_ids[0]).status = FACE_STATUS_ASSIGNED
        db.session.commit()

    response = client.patch("/api/faces", json={"ids": face_ids, "status": FACE_STATUS_IGNORED})

    assert response.status_code == 409


def test_unknown_person_is_not_found(app, client):
    with app.app_context():
        face_ids = _add_unassigned_faces(1)

    response = client.patch("/api/faces", json={"ids": face_ids, "person_id": 999999})

    assert response.status_code == 404
    assert response.get_json()["code"] == "person_not_found"


@pytest.mark.parametrize("payload", [
    {"ids": [1]},
    {"ids": [1], "person_id": 1, "status": FACE_STATUS_IGNORED},
    {"ids": [1], "status": "ASSIGNED"},
])
def test_needs_exactly_one_change(client, payload):
    response = client.patch("/api/faces", json=payload)

    assert response.status_code == 400
    assert response.get_json()["code"] == "assignment_fields_required"


def test_requires_a_json_body(client):
    response = client.patch("/api/faces", data={"ids": "1", "person_id": "1"})

    assert response.status_code == 415
    assert response.get_json()["code"] == "json_required"
