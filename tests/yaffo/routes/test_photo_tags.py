"""The single PUT /api/photo/<id>/tags endpoint replaces a photo's whole tag set in
one request and emits photo_modified (so export_photo_tag-style automations react
once per save). emit_event is stubbed so tests don't enqueue a real dispatch task.
"""
import pytest

from yaffo.db import db
from yaffo.db.models import EVENT_PHOTO_MODIFIED, MediaItem, Tag


@pytest.fixture
def photo_id(app):
    with app.app_context():
        photo = MediaItem(full_file_path="/lib/a.jpg")
        photo.tags = [Tag(tag_name="old", tag_value="1")]
        db.session.add(photo)
        db.session.commit()
        return photo.id


@pytest.fixture(autouse=True)
def captured_events(monkeypatch):
    """Capture (and neutralise) emitted events so no real dispatch task is enqueued."""
    events: list[tuple] = []
    monkeypatch.setattr("yaffo.routes.photos.emit_event",
                        lambda event_type, payload: events.append((event_type, payload)))
    return events


def test_put_replaces_the_tag_set(client, photo_id):
    resp = client.put(f"/api/photo/{photo_id}/tags", json={"tags": [
        {"tag_name": "beach", "tag_value": "maui"},
        {"tag_name": "favorite", "tag_value": ""},
    ]})
    assert resp.status_code == 200
    returned = {(t["tag_name"], t["tag_value"]) for t in resp.get_json()["tags"]}
    assert returned == {("beach", "maui"), ("favorite", None)}  # blank value -> null


def test_put_clears_previous_tags(client, photo_id, app):
    client.put(f"/api/photo/{photo_id}/tags", json={"tags": [{"tag_name": "new"}]})
    with app.app_context():
        rows = db.session.query(Tag).filter_by(media_item_id=photo_id).all()
        assert [t.tag_name for t in rows] == ["new"]  # 'old' is gone


def test_put_emits_photo_modified(client, photo_id, captured_events):
    client.put(f"/api/photo/{photo_id}/tags", json={"tags": []})
    assert captured_events == [(EVENT_PHOTO_MODIFIED, {"media_ids": [photo_id]})]


def test_validation_failure_does_not_emit(client, photo_id, captured_events):
    resp = client.put(f"/api/photo/{photo_id}/tags", json={"tags": [{"tag_name": "  "}]})
    assert resp.status_code == 400
    assert captured_events == []


def test_rejects_non_list_tags(client, photo_id):
    assert client.put(f"/api/photo/{photo_id}/tags", json={"tags": "nope"}).status_code == 400


def test_missing_photo_is_404(client):
    assert client.put("/api/photo/999999/tags", json={"tags": []}).status_code == 404


# --- POST /api/photo/<id>/favorite ----------------------------------------

def test_favorite_toggles_true_then_null(client, photo_id, app):
    first = client.post(f"/api/photo/{photo_id}/favorite")
    assert first.status_code == 200 and first.get_json()["favorite"] is True
    second = client.post(f"/api/photo/{photo_id}/favorite")
    assert second.get_json()["favorite"] is None  # unset, not False
    with app.app_context():
        assert db.session.get(MediaItem, photo_id).favorite is None


def test_favorite_emits_photo_modified(client, photo_id, captured_events):
    client.post(f"/api/photo/{photo_id}/favorite")
    assert captured_events == [(EVENT_PHOTO_MODIFIED, {"media_ids": [photo_id]})]


def test_favorite_missing_photo_is_404(client):
    assert client.post("/api/photo/999999/favorite").status_code == 404
