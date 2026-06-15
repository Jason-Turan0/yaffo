"""Route tests for the automation builder (chat / status / publish / discard).

Use the shared throwaway-DB app fixture. The chat happy path (which enqueues a real
generation) isn't exercised here; the request-side gating and the publish/status/
discard endpoints are.
"""
import pytest

from yaffo.db import db
from yaffo.db.models import (
    Automation,
    AUTOMATION_STATUS_ACCEPTED,
    AUTOMATION_STATUS_READY,
)

pytestmark = pytest.mark.unit


def _add(app, **kw):
    defaults = dict(slug="a1", name="A1", is_system=False, status=AUTOMATION_STATUS_READY)
    defaults.update(kw)
    with app.app_context():
        db.session.add(Automation(**defaults))
        db.session.commit()


def test_status_returns_code_and_messages(app, client):
    _add(app, working_code="draft", published_code="live")
    resp = client.get("/utilities/automations/a1/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["slug"] == "a1"
    assert body["working_code"] == "draft"
    assert body["published_code"] == "live"
    assert body["messages"] == []


def test_publish_promotes_working_to_published(app, client):
    _add(app, working_code="print('new')", published_code=None)
    resp = client.post("/utilities/automations/a1/publish")
    assert resp.status_code == 204
    with app.app_context():
        a = db.session.query(Automation).filter_by(slug="a1").first()
        assert a.published_code == "print('new')"
        assert a.status == AUTOMATION_STATUS_ACCEPTED


def test_publish_without_draft_409(app, client):
    _add(app, working_code=None, published_code="live")
    assert client.post("/utilities/automations/a1/publish").status_code == 409


def test_discard_clears_working_draft(app, client):
    _add(app, working_code="draft", published_code="live")
    assert client.post("/utilities/automations/a1/discard").status_code == 204
    with app.app_context():
        assert db.session.query(Automation).filter_by(slug="a1").first().working_code is None


def test_chat_on_system_automation_rejected(app, client):
    _add(app, slug="sys", name="Sys", is_system=True, handler="file_sync")
    resp = client.post("/utilities/automations/sys/chat", json={"message": "do x"})
    assert resp.status_code == 400


def test_chat_without_api_key_rejected(app, client):
    # the routes conftest neutralises the API key by default
    _add(app)
    resp = client.post("/utilities/automations/a1/chat", json={"message": "build me a thing"})
    assert resp.status_code == 400
    assert "API key" in resp.get_json()["error"]


def test_chat_missing_message_400(app, client):
    _add(app)
    assert client.post("/utilities/automations/a1/chat", json={}).status_code == 400


def test_unknown_automation_404(app, client):
    assert client.get("/utilities/automations/nope/status").status_code == 404


def test_index_redirects_to_first(app, client):
    _add(app, slug="z", name="Zeta")
    resp = client.get("/utilities/automations")
    assert resp.status_code == 302
    assert "/utilities/automations/z" in resp.headers["Location"]


def test_show_renders(app, client):
    _add(app, name="Renders")
    resp = client.get("/utilities/automations/a1")
    assert resp.status_code == 200
    assert "Renders" in resp.get_data(as_text=True)


def test_create_adds_custom_automation(app, client):
    resp = client.post("/utilities/automations/create", data={"name": "Tag beaches"})
    assert resp.status_code == 302
    with app.app_context():
        a = db.session.query(Automation).filter_by(slug="tag-beaches").first()
        assert a is not None and a.is_system is False and a.enabled is False


def test_create_requires_name(app, client):
    assert client.post("/utilities/automations/create", data={"name": "  "}).status_code == 400


def test_delete_removes_custom(app, client):
    _add(app)
    assert client.post("/utilities/automations/a1/delete").status_code == 302
    with app.app_context():
        assert db.session.query(Automation).filter_by(slug="a1").first() is None


def test_delete_system_rejected(app, client):
    _add(app, slug="sys", name="Sys", is_system=True, handler="file_sync")
    assert client.post("/utilities/automations/sys/delete").status_code == 400


def test_toggle_enabled_flips(app, client):
    _add(app, enabled=False)
    assert client.post("/utilities/automations/a1/enabled").status_code == 204
    with app.app_context():
        assert db.session.query(Automation).filter_by(slug="a1").first().enabled is True


def test_cancel_settles_to_accepted(app, client):
    from yaffo.db.models import AUTOMATION_STATUS_IN_PROGRESS
    _add(app, status=AUTOMATION_STATUS_IN_PROGRESS)
    assert client.post("/utilities/automations/a1/cancel").status_code == 204
    with app.app_context():
        assert db.session.query(Automation).filter_by(slug="a1").first().status == AUTOMATION_STATUS_ACCEPTED
