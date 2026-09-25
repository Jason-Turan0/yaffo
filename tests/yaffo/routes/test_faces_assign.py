"""/api/faces/assign takes any number of faces in one request.

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
        "yaffo.routes.faces.assign_faces_to_person",
        lambda person_id, face_ids: queued.append((person_id, face_ids)),
    )
    with app.app_context():
        face_ids = _add_unassigned_faces(FACE_COUNT)
        person = Person(name="Ada")
        db.session.add(person)
        db.session.commit()
        person_id = person.id

    response = client.post(
        "/api/faces/assign",
        json={"faces": face_ids, "person": person_id, "faceStatus": FACE_STATUS_ASSIGNED},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["code"] == "faces_assigned"
    assert body["face_ids"] == face_ids
    assert queued == [(person_id, face_ids)]
    with app.app_context():
        statuses = {status for (status,) in db.session.query(Face.status)}
        assert statuses == {FACE_STATUS_PROCESSING}


def test_ignores_every_selected_face_with_no_per_request_limit(app, client):
    with app.app_context():
        face_ids = _add_unassigned_faces(FACE_COUNT)

    response = client.post(
        "/api/faces/assign",
        json={"faces": face_ids, "person": None, "faceStatus": FACE_STATUS_IGNORED},
    )

    assert response.status_code == 200
    assert response.get_json()["code"] == "faces_ignored"
    with app.app_context():
        statuses = {status for (status,) in db.session.query(Face.status)}
        assert statuses == {FACE_STATUS_IGNORED}


@pytest.mark.parametrize("faces", ["12", 12, {"1": 1}])
def test_rejects_faces_that_are_not_a_list(client, faces):
    response = client.post(
        "/api/faces/assign",
        json={"faces": faces, "person": None, "faceStatus": FACE_STATUS_IGNORED},
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_face_ids"
