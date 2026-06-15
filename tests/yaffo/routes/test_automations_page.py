"""Route tests for the automation builder (chat / status / publish / discard).

Use the shared throwaway-DB app fixture. The chat happy path (which enqueues a real
generation) isn't exercised here; the request-side gating and the publish/status/
discard endpoints are.
"""
import pytest

from yaffo.db import db
from yaffo.db.models import (
    Automation,
    AutomationTrigger,
    AUTOMATION_STATUS_ACCEPTED,
    AUTOMATION_STATUS_READY,
    TRIGGER_TYPE_EVENT,
    TRIGGER_TYPE_SCHEDULE,
)

pytestmark = pytest.mark.unit


def _add(app, **kw):
    defaults = dict(slug="a1", name="A1", is_system=False, status=AUTOMATION_STATUS_READY)
    defaults.update(kw)
    with app.app_context():
        db.session.add(Automation(**defaults))
        db.session.commit()


def _add_trigger(app, slug="a1", **kw):
    with app.app_context():
        automation = db.session.query(Automation).filter_by(slug=slug).first()
        trigger = AutomationTrigger(automation_id=automation.id, **kw)
        db.session.add(trigger)
        db.session.commit()
        return trigger.id


def _triggers(app, slug="a1"):
    with app.app_context():
        automation = db.session.query(Automation).filter_by(slug=slug).first()
        return list(automation.triggers)


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


def test_save_schedule_adds_trigger(app, client):
    # the cron is composed client-side by the builder and posted as `cron`; an empty
    # edit_trigger_id means "add", not "edit"
    _add(app)
    resp = client.post(
        "/utilities/automations/a1/triggers",
        data={"action": "save_schedule", "cron": "*/30 * * * *", "edit_trigger_id": ""},
    )
    assert resp.status_code == 200
    triggers = _triggers(app)
    assert len(triggers) == 1
    assert triggers[0].trigger_type == TRIGGER_TYPE_SCHEDULE
    assert triggers[0].cron == "*/30 * * * *"
    assert triggers[0].enabled is True
    assert triggers[0].next_run_at is None  # left for the dispatcher to initialise


def test_save_schedule_rejects_bad_cron(app, client):
    _add(app)
    resp = client.post(
        "/utilities/automations/a1/triggers",
        data={"action": "save_schedule", "cron": "not a cron"},
    )
    assert resp.status_code == 200
    assert "valid 5-field cron" in resp.get_data(as_text=True)
    assert _triggers(app) == []


def test_save_schedule_edits_existing(app, client):
    from datetime import datetime
    _add(app)
    tid = _add_trigger(
        app, trigger_type=TRIGGER_TYPE_SCHEDULE, enabled=True,
        cron="* * * * *", next_run_at=datetime(2026, 1, 1),
    )
    resp = client.post(
        "/utilities/automations/a1/triggers",
        data={"action": "save_schedule", "cron": "0 9 * * 1", "edit_trigger_id": tid},
    )
    assert resp.status_code == 200
    triggers = _triggers(app)
    assert len(triggers) == 1  # edited in place, not added
    assert triggers[0].cron == "0 9 * * 1"
    assert triggers[0].next_run_at is None  # reset so the dispatcher recomputes


def test_save_schedule_edit_unknown_id_404(app, client):
    _add(app)
    resp = client.post(
        "/utilities/automations/a1/triggers",
        data={"action": "save_schedule", "cron": "0 9 * * 1", "edit_trigger_id": "999"},
    )
    assert resp.status_code == 404


def test_add_event_trigger(app, client):
    _add(app)
    resp = client.post(
        "/utilities/automations/a1/triggers",
        data={"action": "add_event", "new_event_type": "photo_indexed"},
    )
    assert resp.status_code == 200
    triggers = _triggers(app)
    assert len(triggers) == 1
    assert triggers[0].trigger_type == TRIGGER_TYPE_EVENT
    assert triggers[0].event_type == "photo_indexed"


def test_add_event_rejects_unknown_type(app, client):
    _add(app)
    resp = client.post(
        "/utilities/automations/a1/triggers",
        data={"action": "add_event", "new_event_type": "made_up"},
    )
    assert resp.status_code == 200
    assert _triggers(app) == []


def test_remove_trigger(app, client):
    _add(app)
    tid = _add_trigger(app, trigger_type=TRIGGER_TYPE_SCHEDULE, enabled=True, cron="* * * * *")
    resp = client.post(
        "/utilities/automations/a1/triggers",
        data={"action": "remove", "trigger_id": tid},
    )
    assert resp.status_code == 200
    assert _triggers(app) == []


def test_toggle_trigger_enabled(app, client):
    _add(app)
    tid = _add_trigger(app, trigger_type=TRIGGER_TYPE_SCHEDULE, enabled=True, cron="* * * * *")
    client.post(
        "/utilities/automations/a1/triggers",
        data={"action": "toggle", "trigger_id": tid},
    )
    assert _triggers(app)[0].enabled is False


def test_validate_cron_endpoint(app, client):
    assert client.get("/utilities/automations/validate-cron?cron=0+9+*+*+1").get_json() == {"valid": True}
    assert client.get("/utilities/automations/validate-cron?cron=nope").get_json() == {"valid": False}
    assert client.get("/utilities/automations/validate-cron").get_json() == {"valid": False}


def test_trigger_action_on_system_automation_allowed(app, client):
    _add(app, slug="sys", name="Sys", is_system=True, handler="file_sync")
    resp = client.post(
        "/utilities/automations/sys/triggers",
        data={"action": "save_schedule", "cron": "0 * * * *"},
    )
    assert resp.status_code == 200
    assert len(_triggers(app, slug="sys")) == 1


def test_trigger_unknown_action_400(app, client):
    _add(app)
    resp = client.post(
        "/utilities/automations/a1/triggers",
        data={"action": "bogus"},
    )
    assert resp.status_code == 400


def test_trigger_missing_id_404(app, client):
    _add(app)
    resp = client.post(
        "/utilities/automations/a1/triggers",
        data={"action": "remove", "trigger_id": "999"},
    )
    assert resp.status_code == 404


def test_cancel_settles_to_accepted(app, client):
    from yaffo.db.models import AUTOMATION_STATUS_IN_PROGRESS
    _add(app, status=AUTOMATION_STATUS_IN_PROGRESS)
    assert client.post("/utilities/automations/a1/cancel").status_code == 204
    with app.app_context():
        assert db.session.query(Automation).filter_by(slug="a1").first().status == AUTOMATION_STATUS_ACCEPTED
