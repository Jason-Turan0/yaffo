"""The single PUT /media/<id>/tags endpoint replaces a photo's whole tag set in
one request and emits photo_modified (so export_photo_tag-style automations react
once per save). emit_event is stubbed so tests don't enqueue a real dispatch task.
"""
import pytest

from yaffo.db import db
from yaffo.db.models import EVENT_MEDIA_MODIFIED, MediaItem, Tag


@pytest.fixture
def media_item_id(app):
    with app.app_context():
        media_item = MediaItem(full_file_path="/lib/a.jpg")
        media_item.tags = [Tag(tag_name="old", tag_value="1")]
        db.session.add(media_item)
        db.session.commit()
        return media_item.id


@pytest.fixture(autouse=True)
def captured_events(monkeypatch):
    """Capture (and neutralise) emitted events so no real dispatch task is enqueued."""
    events: list[tuple] = []
    for module in ("yaffo.routes.media", "yaffo.routes.api"):
        monkeypatch.setattr(f"{module}.emit_event",
                            lambda event_type, payload: events.append((event_type, payload)))
    return events


def test_put_replaces_the_tag_set(client, media_item_id):
    resp = client.put(f"/media/{media_item_id}/tags", json={"tags": [
        {"tag_name": "beach", "tag_value": "maui"},
        {"tag_name": "favorite", "tag_value": ""},
    ]})
    assert resp.status_code == 200
    returned = {(t["tag_name"], t["tag_value"]) for t in resp.get_json()["tags"]}
    assert returned == {("beach", "maui"), ("favorite", None)}  # blank value -> null


def test_put_clears_previous_tags(client, media_item_id, app):
    client.put(f"/media/{media_item_id}/tags", json={"tags": [{"tag_name": "new"}]})
    with app.app_context():
        rows = db.session.query(Tag).filter_by(media_item_id=media_item_id).all()
        assert [t.tag_name for t in rows] == ["new"]  # 'old' is gone


def test_put_emits_photo_modified(client, media_item_id, captured_events):
    client.put(f"/media/{media_item_id}/tags", json={"tags": []})
    assert captured_events == [(EVENT_MEDIA_MODIFIED, {"media_item_ids": [media_item_id]})]


def test_validation_failure_does_not_emit(client, media_item_id, captured_events):
    resp = client.put(f"/media/{media_item_id}/tags", json={"tags": [{"tag_name": "  "}]})
    assert resp.status_code == 400
    assert captured_events == []


def test_rejects_non_list_tags(client, media_item_id):
    assert client.put(f"/media/{media_item_id}/tags", json={"tags": "nope"}).status_code == 400


def test_missing_photo_is_404(client):
    assert client.put("/media/999999/tags", json={"tags": []}).status_code == 404


# --- PATCH /api/media/<id> (favorite) ---------------------------------------

def test_favorite_sets_true_then_clears_to_null(client, media_item_id, app):
    first = client.patch(f"/api/media/{media_item_id}", json={"favorite": True})
    assert first.status_code == 200
    assert first.get_json() == {"id": media_item_id, "favorite": True}
    second = client.patch(f"/api/media/{media_item_id}", json={"favorite": False})
    assert second.get_json()["favorite"] is False
    with app.app_context():
        assert db.session.get(MediaItem, media_item_id).favorite is None  # unset, not False


def test_favorite_is_idempotent(client, media_item_id, captured_events):
    """Setting the state it already has is a no-op: a retried or doubled click can't
    flip it back, and the export automation isn't woken for nothing."""
    client.patch(f"/api/media/{media_item_id}", json={"favorite": True})
    again = client.patch(f"/api/media/{media_item_id}", json={"favorite": True})
    assert again.get_json()["favorite"] is True
    assert captured_events == [(EVENT_MEDIA_MODIFIED, {"media_item_ids": [media_item_id]})]


@pytest.mark.parametrize("body", [{}, {"favorite": "yes"}, {"favorite": None}])
def test_favorite_must_be_a_boolean(client, media_item_id, body):
    response = client.patch(f"/api/media/{media_item_id}", json=body)
    assert response.status_code == 400
    assert response.get_json()["code"] == "favorite_must_be_boolean"


def test_favorite_missing_photo_is_404(client):
    response = client.patch("/api/media/999999", json={"favorite": True})
    assert response.status_code == 404
    assert response.get_json()["code"] == "photo_not_found"
