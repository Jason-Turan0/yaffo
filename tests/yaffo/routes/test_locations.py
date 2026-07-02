"""Tests for the locations bulk-update route: assigning names and — with the
explicit clear flag — removing them (an empty name alone must never clear)."""
from yaffo.db import db
from yaffo.db.models import MediaItem


def _seed_located_media(app):
    with app.app_context():
        named = MediaItem(
            full_file_path="/media/named.jpg", latitude=43.4, longitude=11.8,
            location_name="Old Town",
        )
        unnamed = MediaItem(
            full_file_path="/media/unnamed.jpg", latitude=43.5, longitude=11.9,
        )
        db.session.add_all([named, unnamed])
        db.session.commit()
        return {"named": named.id, "unnamed": unnamed.id}


def _location_names(app, ids):
    with app.app_context():
        return {
            item.id: item.location_name
            for item in db.session.query(MediaItem).filter(MediaItem.id.in_(ids)).all()
        }


def test_bulk_update_assigns_location_name(client, app):
    ids = _seed_located_media(app)
    response = client.post("/locations/bulk-update", json={
        "media_item_ids": list(ids.values()),
        "location_name": "Test Beach",
    })
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["updated_count"] == 2
    assert payload["location_name"] == "Test Beach"
    assert set(_location_names(app, ids.values()).values()) == {"Test Beach"}


def test_bulk_update_clear_removes_location_names(client, app):
    ids = _seed_located_media(app)
    response = client.post("/locations/bulk-update", json={
        "media_item_ids": list(ids.values()),
        "clear": True,
    })
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["updated_count"] == 2
    assert payload["location_name"] is None
    assert set(_location_names(app, ids.values()).values()) == {None}


def test_bulk_update_rejects_empty_name_without_clear_flag(client, app):
    ids = _seed_located_media(app)
    for body in (
        {"media_item_ids": list(ids.values())},
        {"media_item_ids": list(ids.values()), "location_name": "   "},
        # clear must be exactly true — a truthy string doesn't count
        {"media_item_ids": list(ids.values()), "clear": "yes"},
    ):
        response = client.post("/locations/bulk-update", json=body)
        assert response.status_code == 400
        assert response.get_json()["code"] == "location_fields_required"
    # nothing was touched
    assert _location_names(app, ids.values())[ids["named"]] == "Old Town"


def test_bulk_update_clear_requires_media_item_ids(client):
    response = client.post("/locations/bulk-update", json={"clear": True})
    assert response.status_code == 400
    assert response.get_json()["code"] == "location_fields_required"
