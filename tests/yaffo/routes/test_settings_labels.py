"""Route tests for the classification-label admin on the Settings page: the single
HTMX CRUD endpoint and the re-classify backfill. Uses the shared throwaway-DB app
fixture; the backfill's task enqueue is stubbed."""
import pytest

from yaffo.db import db
from yaffo.db.models import (
    Automation,
    ClassificationLabel,
    Photo,
    PhotoLabel,
    PHOTO_STATUS_INDEXED,
)

pytestmark = pytest.mark.unit


def _add_label(app, name, **kw):
    with app.app_context():
        label = ClassificationLabel(name=name, **kw)
        db.session.add(label)
        db.session.commit()
        return label.id


def test_create_label(app, client):
    resp = client.post("/settings/labels", data={"action": "create", "name": "dog", "prompt": "a dog"})
    assert resp.status_code == 200
    assert "dog" in resp.get_data(as_text=True)
    with app.app_context():
        label = db.session.query(ClassificationLabel).filter_by(name="dog").one()
        assert label.prompt == "a dog"
        assert label.is_default is False


def test_create_requires_name(app, client):
    resp = client.post("/settings/labels", data={"action": "create", "name": "  "})
    assert "Label name is required" in resp.get_data(as_text=True)


def test_create_rejects_duplicate(app, client):
    _add_label(app, "dog")
    resp = client.post("/settings/labels", data={"action": "create", "name": "dog"})
    assert "already exists" in resp.get_data(as_text=True)


def test_toggle_flips_enabled(app, client):
    label_id = _add_label(app, "dog", enabled=True)
    client.post("/settings/labels", data={"action": "toggle", "label_id": label_id})
    with app.app_context():
        assert db.session.get(ClassificationLabel, label_id).enabled is False


def test_delete_removes_label(app, client):
    label_id = _add_label(app, "dog")
    resp = client.post("/settings/labels", data={"action": "delete", "label_id": label_id})
    assert '<span class="label-name">dog</span>' not in resp.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(ClassificationLabel, label_id) is None


def test_reclassify_without_automation_errors(app, client):
    resp = client.post("/settings/labels/reclassify")
    assert "not installed" in resp.get_data(as_text=True)


def test_reclassify_without_photos_errors(app, client):
    with app.app_context():
        db.session.add(Automation(slug="classify_labels", name="Classify labels",
                                  is_system=True, handler="classify_labels"))
        db.session.commit()
    resp = client.post("/settings/labels/reclassify")
    assert "No indexed photos" in resp.get_data(as_text=True)


def test_reclassify_enqueues_over_indexed_photos(app, client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "yaffo.routes.settings.classify_labels_automation_task",
        lambda automation_id, photo_ids: calls.append((automation_id, photo_ids)),
    )
    with app.app_context():
        db.session.add(Automation(slug="classify_labels", name="Classify labels",
                                  is_system=True, handler="classify_labels"))
        db.session.add(Photo(full_file_path="/p/1.jpg", status=PHOTO_STATUS_INDEXED))
        db.session.add(Photo(full_file_path="/p/2.jpg", status=PHOTO_STATUS_INDEXED))
        db.session.commit()
    resp = client.post("/settings/labels/reclassify")
    assert resp.status_code == 200
    assert "Re-classifying 2 photo" in resp.get_data(as_text=True)
    assert len(calls) == 1 and len(calls[0][1]) == 2
