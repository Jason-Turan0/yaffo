"""/api/faces/unassign: the photo detail screen's "Clear" on a face."""
import pytest

from yaffo.db import db
from yaffo.db.models import (
    FACE_STATUS_ASSIGNED,
    FACE_STATUS_IGNORED,
    FACE_STATUS_UNASSIGNED,
    Face,
    MediaItem,
    Person,
    PersonFace,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def recorded(monkeypatch):
    """Record emitted events and rebuilt embeddings instead of doing the work."""
    calls = {"events": [], "embeddings": []}
    monkeypatch.setattr("yaffo.routes.faces.emit_event", lambda name, payload: calls["events"].append((name, payload)))
    monkeypatch.setattr(
        "yaffo.db.repositories.person_repository.update_person_embedding",
        lambda person_id, session: calls["embeddings"].append(person_id),
    )
    return calls


@pytest.fixture
def faces(app):
    with app.app_context():
        photo = MediaItem(full_file_path="/lib/a.jpg")
        person = Person(name="Ada")
        db.session.add_all([photo, person])
        db.session.flush()
        assigned = Face(full_file_path="/f/1.jpg", media_item_id=photo.id, status=FACE_STATUS_ASSIGNED)
        ignored = Face(full_file_path="/f/2.jpg", media_item_id=photo.id, status=FACE_STATUS_IGNORED)
        unassigned = Face(full_file_path="/f/3.jpg", media_item_id=photo.id, status=FACE_STATUS_UNASSIGNED)
        db.session.add_all([assigned, ignored, unassigned])
        db.session.flush()
        db.session.add(PersonFace(person_id=person.id, face_id=assigned.id))
        db.session.commit()
        return {"assigned": assigned.id, "ignored": ignored.id, "unassigned": unassigned.id,
                "person": person.id, "photo": photo.id}


def _status(app, face_id):
    with app.app_context():
        return db.session.get(Face, face_id).status


def test_clears_an_assigned_face(app, client, faces, recorded):
    response = client.post("/api/faces/unassign", json={"faces": [faces["assigned"]]})

    assert response.status_code == 200
    body = response.get_json()
    assert body["code"] == "faces_unassigned" and body["face_ids"] == [faces["assigned"]]
    assert _status(app, faces["assigned"]) == FACE_STATUS_UNASSIGNED
    with app.app_context():
        assert db.session.query(PersonFace).count() == 0
    assert recorded["embeddings"] == [faces["person"]]
    assert recorded["events"] == [("media_modified", {"media_item_ids": [faces["photo"]]})]


def test_clears_an_ignored_face(app, client, faces, recorded):
    response = client.post("/api/faces/unassign", json={"faces": [faces["ignored"]]})

    assert response.status_code == 200
    assert _status(app, faces["ignored"]) == FACE_STATUS_UNASSIGNED
    assert recorded["embeddings"] == []


def test_an_unassigned_face_has_nothing_to_clear(app, client, faces, recorded):
    response = client.post("/api/faces/unassign", json={"faces": [faces["unassigned"]]})

    assert response.status_code == 409
    assert response.get_json()["code"] == "faces_not_assigned"
    assert recorded["events"] == []


@pytest.mark.parametrize("payload", [{}, {"faces": []}, {"faces": "1"}, {"faces": ["x"]}])
def test_rejects_bad_face_ids(client, payload):
    response = client.post("/api/faces/unassign", json=payload)
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_face_ids"


def test_detail_page_marks_each_face_with_its_status(app, client, faces):
    html = client.get(f"/media/view/{faces['photo']}").get_data(as_text=True)
    assert f'data-face-id="{faces["assigned"]}"' in html
    assert 'data-face-status="ASSIGNED"' in html and 'data-face-status="UNASSIGNED"' in html
