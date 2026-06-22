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


def test_status_started_at_is_utc_stamped(app, client):
    """started_at must carry a UTC offset (created_at is naive utcnow) so the chat's
    elapsed counter doesn't read it as local time and go negative."""
    from yaffo.db.repositories import automation_repository as repo
    from yaffo.db.models import CONVERSATION_TYPE_USER

    _add(app)
    with app.app_context():
        automation = db.session.query(Automation).filter_by(slug="a1").first()
        repo.add_message(db.session, automation.id, CONVERSATION_TYPE_USER, "build it")

    started_at = client.get("/utilities/automations/a1/status").get_json()["started_at"]
    assert started_at is not None
    assert started_at.endswith("+00:00")  # offset-aware UTC, not naive


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


def test_update_details_renames_and_describes(app, client):
    _add(app, name="Old", description=None)
    resp = client.post(
        "/utilities/automations/a1/details",
        data={"name": "New name", "description": "Tags beaches"},
    )
    assert resp.status_code == 302
    with app.app_context():
        a = db.session.query(Automation).filter_by(slug="a1").first()
        assert a.name == "New name"
        assert a.description == "Tags beaches"
        assert a.slug == "a1"  # slug stays stable on rename


def test_update_details_blank_description_clears(app, client):
    _add(app, description="something")
    client.post("/utilities/automations/a1/details", data={"name": "A1", "description": "  "})
    with app.app_context():
        assert db.session.query(Automation).filter_by(slug="a1").first().description is None


def test_update_details_requires_name(app, client):
    _add(app)
    assert client.post("/utilities/automations/a1/details", data={"name": " "}).status_code == 400


def test_update_details_system_rejected(app, client):
    _add(app, slug="sys", name="Sys", is_system=True, handler="file_sync")
    assert client.post("/utilities/automations/sys/details", data={"name": "x"}).status_code == 400


def _add_job(app, slug="a1", **kw):
    from yaffo.db.models import Job
    with app.app_context():
        automation = db.session.query(Automation).filter_by(slug=slug).first()
        defaults = dict(name="find_duplicates", status="COMPLETED", automation_id=automation.id)
        defaults.update(kw)
        db.session.add(Job(**defaults))
        db.session.commit()


def test_run_now_fires_automation(app, client, monkeypatch):
    _add(app)
    calls = []
    monkeypatch.setattr(
        "yaffo.routes.utilities.automations.invoke_automation",
        lambda automation, context: calls.append((automation.slug, context)) or True,
    )
    resp = client.post("/utilities/automations/a1/run")
    assert resp.status_code == 202
    assert calls == [("a1", None)]  # fired with no event context, like a schedule tick


def test_run_now_nothing_to_run_400(app, client, monkeypatch):
    _add(app)
    monkeypatch.setattr(
        "yaffo.routes.utilities.automations.invoke_automation",
        lambda automation, context: False,
    )
    resp = client.post("/utilities/automations/a1/run")
    assert resp.status_code == 400
    assert "Nothing to run" in resp.get_json()["error"]


def test_run_now_scoped_runs_over_photos_under_path(app, client, monkeypatch):
    """A path in the body scopes the run: photos under it become the EventContext
    the automation fires with (the live twin of the test-files dry run)."""
    _add(app)
    monkeypatch.setattr(
        "yaffo.routes.utilities.automations.photos_repository.get_photo_ids_under_path",
        lambda session, path: [11, 22, 33],
    )
    calls = []
    monkeypatch.setattr(
        "yaffo.routes.utilities.automations.invoke_automation",
        lambda automation, context: calls.append((automation.slug, context)) or True,
    )
    resp = client.post("/utilities/automations/a1/run", json={"path": "/media/2022"})
    assert resp.status_code == 202
    assert resp.get_json()["photo_count"] == 3
    (slug, context), = calls
    assert slug == "a1"
    assert context.media_ids == [11, 22, 33]
    assert context.event_type == "manual"


def test_run_now_scoped_no_photos_under_path_400(app, client, monkeypatch):
    _add(app)
    monkeypatch.setattr(
        "yaffo.routes.utilities.automations.photos_repository.get_photo_ids_under_path",
        lambda session, path: [],
    )
    fired = []
    monkeypatch.setattr(
        "yaffo.routes.utilities.automations.invoke_automation",
        lambda automation, context: fired.append(context) or True,
    )
    resp = client.post("/utilities/automations/a1/run", json={"path": "/media/empty"})
    assert resp.status_code == 400
    assert "No indexed photos" in resp.get_json()["error"]
    assert fired == []  # didn't fire the automation


def test_run_now_unknown_404(app, client):
    assert client.post("/utilities/automations/nope/run").status_code == 404


def _run_buttons(client, slug="a1"):
    body = client.get(f"/utilities/automations/{slug}").get_data(as_text=True)
    collapsed = "".join(body.split())  # whitespace-insensitive for the JS init args
    return {
        "pickers": 'js-run-files" data-mode="folder"' in body and 'js-run-files" data-mode="file"' in body,
        "plain": 'id="run-automation-button"' in body,
        # 3rd arg of initAutomationRunNow — the hasTriggers flag the click handler
        # uses to warn before running a trigger-less automation.
        "has_triggers_flag": ("window.APP_CONFIG,true" in collapsed),
        "no_triggers_flag": ("window.APP_CONFIG,false" in collapsed),
    }


def test_all_event_triggers_show_run_on_folder_file_buttons(app, client):
    """Every trigger an event → the automation is purely photo-driven → folder/file
    pickers, not the plain Run-now button."""
    _add(app)
    _add_trigger(app, trigger_type=TRIGGER_TYPE_EVENT, enabled=True, event_type="photo_indexed")
    buttons = _run_buttons(client)
    assert buttons["pickers"] and not buttons["plain"]


def test_schedule_trigger_shows_plain_run_now(app, client):
    """A schedule trigger → whole-library context-less Run-now, no pickers, no warning."""
    _add(app)
    _add_trigger(app, trigger_type=TRIGGER_TYPE_SCHEDULE, enabled=True, cron="0 9 * * 1")
    buttons = _run_buttons(client)
    assert buttons["plain"] and not buttons["pickers"]
    assert buttons["has_triggers_flag"]  # has a trigger → no click warning


def test_mixed_triggers_show_plain_run_now(app, client):
    """An event + a schedule trigger → not all events → plain Run-now."""
    _add(app)
    _add_trigger(app, trigger_type=TRIGGER_TYPE_EVENT, enabled=True, event_type="photo_indexed")
    _add_trigger(app, trigger_type=TRIGGER_TYPE_SCHEDULE, enabled=True, cron="0 9 * * 1")
    buttons = _run_buttons(client)
    assert buttons["plain"] and not buttons["pickers"]


def test_no_triggers_show_plain_run_now_with_warning_flag(app, client):
    """No triggers → plain Run-now, and the init is wired with hasTriggers=false so the
    click handler warns the automation won't run on its own."""
    _add(app)
    buttons = _run_buttons(client)
    assert buttons["plain"] and not buttons["pickers"]
    assert buttons["no_triggers_flag"]


def test_run_view_summarizes_batch_job():
    from yaffo.routes.utilities.automations import _run_view
    from yaffo.db.models import Job
    job = Job(id="j", name="find_duplicates", status="COMPLETED",
              task_count=120, completed_count=118, error_count=2)
    view = _run_view(job)
    assert view.summary == "118 of 120 processed, 2 errors"
    assert view.is_finished is True
    assert view.is_error is True  # error_count > 0


def test_run_view_uses_message_for_single_task_run():
    from yaffo.routes.utilities.automations import _run_view
    from yaffo.db.models import Job
    job = Job(id="j", name="my-automation", status="COMPLETED",
              task_count=1, completed_count=1, message="My automation")
    view = _run_view(job)
    assert view.summary == "My automation"
    assert view.is_error is False


def test_run_view_flags_failed():
    from yaffo.routes.utilities.automations import _run_view
    from yaffo.db.models import Job
    job = Job(id="j", name="x", status="FAILED", task_count=1, error="boom")
    view = _run_view(job)
    assert view.is_error is True
    assert view.error == "boom"


def test_run_view_computes_progress_for_in_progress():
    from yaffo.routes.utilities.automations import _run_view
    from yaffo.db.models import Job
    job = Job(id="j", name="find_duplicates", status="RUNNING",
              task_count=50, completed_count=10, error_count=2)
    view = _run_view(job)
    assert view.is_finished is False
    assert view.progress == 24  # (10 + 2) / 50


def test_run_view_progress_zero_when_no_task_count():
    from yaffo.routes.utilities.automations import _run_view
    from yaffo.db.models import Job
    job = Job(id="j", name="x", status="RUNNING", task_count=0)
    assert _run_view(job).progress == 0


def test_runs_fragment_polls_and_shows_in_progress(app, client):
    _add(app)
    _add_job(app, id="running", status="RUNNING", task_count=50,
             completed_count=10, error_count=0)
    body = client.get("/utilities/automations/a1/runs").get_data(as_text=True)
    assert 'hx-trigger="every 5s"' in body  # self-polls
    assert "status-running" in body
    assert "20%" in body  # 10 / 50
    assert "10 of 50 processed" in body


def test_runs_fragment_unknown_404(app, client):
    assert client.get("/utilities/automations/nope/runs").status_code == 404


def test_detail_page_shows_run_history(app, client):
    _add(app)
    _add_job(app, id="run1", task_count=10, completed_count=10)
    body = client.get("/utilities/automations/a1").get_data(as_text=True)
    assert "Run history" in body
    assert "10 of 10 processed" in body


def test_detail_page_run_history_empty_state(app, client):
    _add(app, enabled=False)
    body = client.get("/utilities/automations/a1").get_data(as_text=True)
    assert "Run history" in body
    assert "No runs yet" in body


def test_recent_jobs_newest_first_and_scoped(app, client):
    from datetime import datetime
    from yaffo.db.repositories import automation_repository as repo
    _add(app, slug="a1")
    _add(app, slug="a2", name="A2")
    _add_job(app, slug="a1", id="old", created_at=datetime(2026, 1, 1))
    _add_job(app, slug="a1", id="new", created_at=datetime(2026, 6, 1))
    _add_job(app, slug="a2", id="other")
    with app.app_context():
        a1 = db.session.query(Automation).filter_by(slug="a1").first()
        jobs = repo.get_recent_jobs(db.session, a1.id)
        assert [j.id for j in jobs] == ["new", "old"]  # newest first, a2's job excluded


def _add_system_assign_faces(app):
    """The seeded auto-assign-faces system automation, the one row that exposes a
    configurable threshold (see background_tasks.automation_config)."""
    _add(
        app, slug="auto_assign_faces", name="Auto-assign faces",
        is_system=True, handler="auto_assign_faces", config={"threshold": 60},
    )


def test_config_saves_valid_threshold(app, client):
    _add_system_assign_faces(app)
    resp = client.post(
        "/utilities/automations/auto_assign_faces/config", data={"threshold": "70"}
    )
    assert resp.status_code == 302
    with app.app_context():
        a = db.session.query(Automation).filter_by(slug="auto_assign_faces").first()
        assert a.config == {"threshold": 70}  # 0-100 UI scale, stored as int


def test_config_rejects_out_of_range(app, client):
    _add_system_assign_faces(app)
    resp = client.post(
        "/utilities/automations/auto_assign_faces/config", data={"threshold": "150"}
    )
    assert resp.status_code == 400
    with app.app_context():
        a = db.session.query(Automation).filter_by(slug="auto_assign_faces").first()
        assert a.config == {"threshold": 60}  # unchanged


def test_config_rejects_non_numeric(app, client):
    _add_system_assign_faces(app)
    resp = client.post(
        "/utilities/automations/auto_assign_faces/config", data={"threshold": "high"}
    )
    assert resp.status_code == 400


def test_config_rejected_for_non_configurable(app, client):
    _add(app)  # a plain custom automation declares no config fields
    assert client.post("/utilities/automations/a1/config", data={"threshold": "0.8"}).status_code == 400


def test_detail_page_shows_configure_for_configurable(app, client):
    _add_system_assign_faces(app)
    body = client.get("/utilities/automations/auto_assign_faces").get_data(as_text=True)
    assert "configure-automation-button" in body
    assert "configureAutomationModal" in body


def test_detail_page_hides_configure_when_no_config(app, client):
    _add(app)
    body = client.get("/utilities/automations/a1").get_data(as_text=True)
    assert "configure-automation-button" not in body


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


def test_edit_triggers_page_renders(app, client):
    _add(app, name="Renders")
    _add_trigger(app, trigger_type=TRIGGER_TYPE_SCHEDULE, enabled=True, cron="0 9 * * 1")
    resp = client.get("/utilities/automations/a1/triggers/edit")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Renders" in body
    assert "data-cron-builder" in body  # the full editor is present on this screen


def test_edit_triggers_unknown_404(app, client):
    assert client.get("/utilities/automations/nope/triggers/edit").status_code == 404


def test_detail_page_shows_edit_triggers_link(app, client):
    _add(app)
    body = client.get("/utilities/automations/a1").get_data(as_text=True)
    assert "/utilities/automations/a1/triggers/edit" in body
    assert "data-cron-builder" not in body  # editor moved off the detail page


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


def test_test_files_runs_against_photos_under_path(app, client):
    from yaffo.db.models import MediaItem
    _add(app, published_code="print(len(ctx['media_ids']))")
    with app.app_context():
        db.session.add_all([
            MediaItem(full_file_path="/media/trip/a.jpg"),
            MediaItem(full_file_path="/media/trip/b.jpg"),
            MediaItem(full_file_path="/media/other/c.jpg"),
        ])
        db.session.commit()
    data = client.post("/utilities/automations/a1/test-files", json={"path": "/media/trip"}).get_json()
    assert data["context"]["type"] == "files"
    assert len(data["context"]["photo_ids"]) == 2
    assert data["output"] == ["2"]


def test_test_files_prefers_working_code(app, client):
    _add(app, working_code="print('draft')", published_code="print('live')")
    data = client.post("/utilities/automations/a1/test-files", json={"path": "/x"}).get_json()
    assert data["code_source"] == "working"
    assert data["output"] == ["draft"]


def test_test_files_records_mutating_action_without_performing(app, client):
    from yaffo.db.models import MediaItem, Tag
    _add(app, published_code="tag_photos([{'photo_id': pid, 'name': 'beach'} for pid in ctx['media_ids']])")
    with app.app_context():
        db.session.add(MediaItem(full_file_path="/media/a.jpg"))
        db.session.commit()
    data = client.post("/utilities/automations/a1/test-files", json={"path": "/media/a.jpg"}).get_json()
    assert data["success"] is True
    assert data["actions"][0]["summary"] == "Tag 1 photo(s)"
    assert data["actions"][0]["name"] == "tag_photos"
    with app.app_context():
        assert db.session.query(Tag).count() == 0  # dry run performed no tagging


def test_test_files_requires_path(app, client):
    _add(app, published_code="print('x')")
    assert client.post("/utilities/automations/a1/test-files", json={}).status_code == 400


def test_test_files_no_code_400(app, client):
    _add(app, working_code=None, published_code=None)
    assert client.post("/utilities/automations/a1/test-files", json={"path": "/x"}).status_code == 400


def test_test_files_unknown_404(app, client):
    assert client.post("/utilities/automations/nope/test-files", json={"path": "/x"}).status_code == 404


def test_cancel_settles_to_accepted(app, client):
    from yaffo.db.models import AUTOMATION_STATUS_IN_PROGRESS
    _add(app, status=AUTOMATION_STATUS_IN_PROGRESS)
    assert client.post("/utilities/automations/a1/cancel").status_code == 204
    with app.app_context():
        assert db.session.query(Automation).filter_by(slug="a1").first().status == AUTOMATION_STATUS_ACCEPTED
