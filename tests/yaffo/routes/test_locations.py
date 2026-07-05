"""Tests for the locations routes: the map page (which renders the shared filter
sidebar and a marker payload rich enough for client-side filtering) and the
bulk-update route: assigning names and — with the explicit clear flag —
removing them (an empty name alone must never clear)."""
import json
import re

from yaffo.db import db
from yaffo.db.models import (
    ClassificationLabel,
    Face,
    MediaItem,
    MediaLabel,
    Person,
    PersonFace,
    Tag,
)


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


def _map_payload(body: str) -> list[dict]:
    """The locations array the page hands to initMap()."""
    match = re.search(r"initMap\(\s*(\[.*?\])\s*,\s*app\.i18n", body, re.DOTALL)
    assert match, "locations payload not found on the page"
    return json.loads(match.group(1))


class TestLocationsPageFilterPanel:
    """The map page renders the same sidebar as the home gallery, but filters
    its markers client-side — so each marker carries the filterable fields."""

    def test_renders_filter_sidebar_and_config_modal(self, client, app):
        _seed_located_media(app)
        body = client.get("/locations").data.decode()

        assert 'id="filter-form"' in body
        assert 'id="configure-filters-btn"' in body
        assert 'id="configureFiltersModal"' in body
        # the layout saves to this page's scope, not home's
        assert 'data-page="locations"' in body
        assert "initClientFilter" in body

    def test_locations_layout_controls_sidebar(self, client, app):
        _seed_located_media(app)
        resp = client.post("/settings/filters/locations", json={"items": [
            {"key": "year", "visible": False},
        ]})
        assert resp.status_code == 204

        body = client.get("/locations").data.decode()
        assert 'id="year-select"' not in body
        assert 'id="device-select"' in body

    def test_marker_payload_carries_filterable_fields(self, client, app):
        with app.app_context():
            person = Person(name="Ada", gender=0)
            label = ClassificationLabel(name="beach", enabled=True)
            db.session.add_all([person, label])
            db.session.flush()

            item = MediaItem(
                full_file_path="/media/2021/IMG_1.jpg",
                latitude=43.4, longitude=11.8, location_name="Old Town",
                year=2021, month=7, device="FUJIFILM X-T4", favorite=True,
            )
            db.session.add(item)
            db.session.flush()

            # one face assigned to Ada, one unassigned face with an estimate only
            assigned = Face(full_file_path="/faces/a.jpg", media_item_id=item.id, gender=1)
            unassigned = Face(full_file_path="/faces/b.jpg", media_item_id=item.id, gender=1)
            db.session.add_all([assigned, unassigned])
            db.session.flush()
            db.session.add_all([
                PersonFace(person_id=person.id, face_id=assigned.id),
                MediaLabel(media_item_id=item.id, label_id=label.id),
                Tag(media_item_id=item.id, tag_name="Event", tag_value="Vacation"),
            ])
            db.session.commit()
            person_id, label_id, item_id = person.id, label.id, item.id

        payload = _map_payload(client.get("/locations").data.decode())
        marker = next(m for m in payload if m["id"] == item_id)

        assert marker["name"] == "Old Town"
        assert marker["year"] == 2021 and marker["month"] == 7
        assert marker["device"] == "FUJIFILM X-T4"
        assert marker["favorite"] is True
        assert marker["person_ids"] == [person_id]
        # person gender (0) overrides the assigned face's estimate; the
        # unassigned face keeps its estimate (1)
        assert marker["genders"] == [0, 1]
        assert marker["label_ids"] == [label_id]
        assert marker["tags"] == [{"name": "Event", "value": "Vacation"}]

    def test_items_without_coordinates_stay_off_the_map(self, client, app):
        with app.app_context():
            db.session.add(MediaItem(full_file_path="/media/nowhere.jpg"))
            db.session.commit()

        assert _map_payload(client.get("/locations").data.decode()) == []


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
