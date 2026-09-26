"""Routes for Ask Yaffo. The background run is replaced by a recorder, so these
cover the HTTP contract: starting turns, polling, cancel, rename/delete, the
settings switches, and availability (setting and demo mode)."""
import json

import pytest

from yaffo.db import db
from yaffo.db.models import ASSISTANT_STATUS_FAILED, ASSISTANT_STATUS_IDLE, ASSISTANT_STATUS_RUNNING, Job, MediaItem
from yaffo.db.repositories import assistant_repository as repo
from yaffo.site_agents.assistant import settings as assistant_settings

pytestmark = pytest.mark.unit


@pytest.fixture
def runs(monkeypatch):
    """Record enqueued runs instead of running them."""
    started = []
    monkeypatch.setattr("yaffo.routes.assistant.assistant_run_task", started.append)
    return started


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr("yaffo.site_agents.llm_config.get_api_key", lambda *a, **k: "key")


def _start(client, message="How do I add folders?"):
    return client.post("/api/assistant/conversations", json={"message": message})


def test_first_message_creates_a_titled_conversation_and_starts_a_run(client, runs, with_key):
    response = _start(client)

    assert response.status_code == 202
    conversation = response.get_json()["conversation"]
    assert conversation["title"] == "How do I add folders?"
    assert conversation["status"] == ASSISTANT_STATUS_RUNNING
    assert runs == [conversation["id"]]


def test_poll_returns_the_chat_dialog_body(client, runs, with_key):
    conversation_id = _start(client).get_json()["conversation"]["id"]
    repo.add_event(db.session, conversation_id, "tool", "", {"tool": "search_docs", "count": 1})

    body = client.get(f"/api/assistant/conversations/{conversation_id}").get_json()

    assert body["status"] == ASSISTANT_STATUS_RUNNING
    assert body["started_at"].endswith("+00:00")
    assert [(m["type"], m["seq"]) for m in body["messages"]] == [("user", 0), ("tool", 1)]
    assert body["messages"][1]["payload"] == {"tool": "search_docs", "count": 1}
    assert body["conversation"]["id"] == conversation_id


def test_poll_says_why_a_queued_reply_has_not_started(client, runs, with_key, queue_store):
    conversation_id = _start(client).get_json()["conversation"]["id"]
    url = f"/api/assistant/conversations/{conversation_id}"
    assert client.get(url).get_json()["queue"] is None  # nothing queued in this test queue

    busy = queue_store.insert_task("index_photo_task", [], {})
    queue_store.mark_running(busy)
    queue_store.insert_task("assistant_run_task", [conversation_id], {}, priority=10)
    queue_store.write_heartbeat(pid=1, started_at=0, workers=1, busy=1)

    queue = client.get(url).get_json()["queue"]
    assert queue["state"] == "waiting"
    assert queue["busy_with"] == "index_photo_task"
    assert "busy indexing photos" in queue["message"]

    repo.set_status(db.session, conversation_id, ASSISTANT_STATUS_IDLE)
    assert client.get(url).get_json()["queue"] is None  # only while the run is active


def test_follow_up_is_refused_while_answering_then_accepted(client, runs, with_key):
    conversation_id = _start(client).get_json()["conversation"]["id"]
    url = f"/api/assistant/conversations/{conversation_id}/messages"

    busy = client.post(url, json={"message": "and then?"})
    assert busy.status_code == 409
    assert busy.get_json()["code"] == "run_in_progress"

    repo.set_status(db.session, conversation_id, ASSISTANT_STATUS_FAILED)
    assert client.post(url, json={"message": "and then?"}).status_code == 202
    assert runs == [conversation_id, conversation_id]
    kinds = [(e.kind, e.content) for e in repo.list_events(db.session, conversation_id)]
    assert kinds[-1] == ("user", "and then?")


@pytest.mark.parametrize("payload, code", [
    ({}, "message_required"),
    ({"message": "   "}, "message_required"),
    ({"message": "x" * 4001}, "message_too_long"),
])
def test_invalid_messages(client, runs, with_key, payload, code):
    response = client.post("/api/assistant/conversations", json=payload)
    assert response.status_code == 400
    assert response.get_json()["code"] == code
    assert runs == []


def test_missing_api_key_is_refused_before_anything_is_saved(client, runs):
    response = _start(client)
    assert response.status_code == 400
    assert response.get_json()["code"] == "api_key_missing"
    assert "Anthropic" in response.get_json()["error"]
    assert repo.count_conversations(db.session) == 0


def test_cancel_list_rename_and_delete(client, runs, with_key):
    conversation_id = _start(client).get_json()["conversation"]["id"]
    base = f"/api/assistant/conversations/{conversation_id}"

    assert client.post(f"{base}/cancel").status_code == 204
    assert repo.get_conversation(db.session, conversation_id).status == ASSISTANT_STATUS_IDLE

    assert client.patch(base, json={"title": "  Folders  "}).status_code == 204
    listed = client.get("/api/assistant/conversations").get_json()["conversations"]
    assert [(c["id"], c["title"]) for c in listed] == [(conversation_id, "Folders")]
    assert client.patch(base, json={"title": " "}).get_json()["code"] == "title_required"

    assert client.delete(base).status_code == 204
    assert client.delete(base).status_code == 404
    assert client.get(base).status_code == 404


def test_delete_all(client, runs, with_key):
    _start(client)
    _start(client, "Another")
    body = client.post("/api/assistant/conversations/delete-all").get_json()
    assert body == {"deleted": 2}
    assert repo.count_conversations(db.session) == 0


def test_turned_off_hides_everything(client, runs, with_key):
    assistant_settings.set_enabled(False)

    assert _start(client).status_code == 404
    assert client.get("/api/assistant/conversations").status_code == 404
    assert client.get("/assistant").status_code == 404
    assert 'id="assistant-open"' not in client.get("/settings").get_data(as_text=True)


def test_navbar_button_and_panel_render_when_on(client, with_key):
    home = client.get("/settings").get_data(as_text=True)
    assert 'id="assistant-open"' in home
    assert 'id="assistant-panel"' in home
    assert 'data-assistant-mode="floating"' in home

    page = client.get("/assistant").get_data(as_text=True)
    assert page.count('id="assistant-panel"') == 1
    assert 'data-assistant-mode="page"' in page


def test_floating_button_needs_an_api_key(client, monkeypatch):
    # No key (the route-test default): neither entry point is shown.
    html = client.get("/settings").get_data(as_text=True)
    assert 'id="assistant-open"' not in html
    assert 'id="assistant-fab"' not in html

    monkeypatch.setattr("yaffo.site_agents.llm_config.get_api_key", lambda *a, **k: "key")
    html = client.get("/settings").get_data(as_text=True)
    assert 'id="assistant-fab"' in html
    assert 'class="nav-assistant-item"' in html
    # The full page has the chat inline; no floating entry there.
    assert 'id="assistant-fab"' not in client.get("/assistant").get_data(as_text=True)


def test_settings_section_and_switches(client):
    html = client.get("/settings").get_data(as_text=True)
    assert 'id="assistant-section"' in html
    assert 'id="assistant-model"' not in html

    off = client.post("/settings/assistant/enabled", data={})
    assert off.headers["HX-Refresh"] == "true"
    assert assistant_settings.is_enabled() is False
    client.post("/settings/assistant/enabled", data={"enabled": "on"})
    assert assistant_settings.is_enabled() is True

    model = client.post("/settings/assistant/model", data={"model": "claude-sonnet-4-6"})
    assert model.status_code == 404
    assert assistant_settings.resolve_model() == "claude-haiku-4-5-20251001"


def test_demo_mode_hides_the_assistant(app, client, runs, with_key):
    app.config["DEMO_MODE"] = True
    try:
        assert client.get("/api/assistant/conversations").status_code == 404
        assert 'id="assistant-open"' not in client.get("/").get_data(as_text=True)
    finally:
        app.config["DEMO_MODE"] = False


def test_empty_conversation_notice_links_to_settings(client, with_key):
    html = client.get("/").get_data(as_text=True)
    assert 'id="assistant-notice-template"' in html
    assert "it can check this computer" in html
    assert 'href="/settings#assistant-section"' in html

    for group in assistant_settings.DIAGNOSTIC_GROUPS:
        assistant_settings.set_diagnostics_enabled(group, False)
    html = client.get("/").get_data(as_text=True)
    assert "documentation only. Nothing from this computer is sent." in html
    assert "it can check this computer" not in html


def test_attached_context_is_allowlisted_and_capped(client, runs, with_key):
    response = client.post("/api/assistant/conversations", json={
        "message": "Why did this fail?",
        "context": {"page": "Utilities → Index Photos", "error": "x" * 900, "job_id": "abc",
                    "api_key": "sk-nope", "error_code": True},
    })
    conversation_id = response.get_json()["conversation"]["id"]
    user = repo.list_events(db.session, conversation_id)[0]
    context = json.loads(user.payload)["context"]
    assert set(context) == {"page", "error", "job_id"}
    assert len(context["error"]) == 500
    assert repo.latest_user_context(db.session, conversation_id) == context


def test_diagnostics_switches(client):
    html = client.get("/settings").get_data(as_text=True)
    assert 'id="assistant-diag-logs"' in html
    # People-name redaction was removed; the setting and its route are gone.
    assert 'id="assistant-redact-people"' not in html
    assert client.post("/settings/assistant/redact-people", data={"enabled": "on"}).status_code in (404, 405)

    client.post("/settings/assistant/diagnostics/files", data={})
    assert "files" not in assistant_settings.enabled_diagnostics()
    client.post("/settings/assistant/diagnostics/files", data={"enabled": "on"})
    assert "files" in assistant_settings.enabled_diagnostics()
    assert client.post("/settings/assistant/diagnostics/bogus", data={}).status_code == 404



def test_flash_help_escapes_context_and_requires_ready_assistant(client, with_key, monkeypatch):
    with client.session_transaction() as session:
        session["_flashes"] = [("error", '<img src=x onerror=alert(1)>')]
    html = client.get("/assistant").get_data(as_text=True)
    assert 'data-assistant-help' in html
    assert 'class="message-action" data-icon="assistant"' in html
    assert 'data-error="&lt;img src=x onerror=alert(1)&gt;"' in html
    assert "metadata" not in assistant_settings.enabled_diagnostics()
    monkeypatch.setattr("yaffo.site_agents.llm_config.get_api_key", lambda *a, **k: None)
    with client.session_transaction() as session:
        session["_flashes"] = [("error", "Failed")]
    html = client.get("/settings").get_data(as_text=True)
    assert 'data-assistant-help\n' not in html


def test_metadata_switch_is_independent_and_opt_in(client):
    assert "metadata" not in assistant_settings.enabled_diagnostics()
    assert client.post("/settings/assistant/diagnostics/metadata", data={"enabled": "on"}).status_code == 200
    assert "metadata" in assistant_settings.enabled_diagnostics()
    client.post("/settings/assistant/diagnostics/metadata", data={})
    assert "metadata" not in assistant_settings.enabled_diagnostics()


def test_settings_has_no_contextual_help_even_for_errors(client, with_key):
    with client.session_transaction() as session:
        session["_flashes"] = [("error", "Something failed")]
    html = client.get("/settings").get_data(as_text=True)
    assert 'data-assistant-help-disabled' in html
    assert 'data-assistant-help\n' not in html
    assert 'class="message-action"' not in html
    assert 'id="assistant-diag-metadata"' in html


@pytest.mark.parametrize("status,error,expected", [
    ("FAILED", None, True), ("RUNNING", "Could not read media", True),
    ("COMPLETED", None, False),
])
def test_job_help_is_shown_for_failed_or_error_cards(client, with_key, status, error, expected):
    db.session.add(Job(id="help-job", name="index_photos", status=status, error=error,
                       task_count=1, completed_count=0, error_count=0, cancelled_count=0, message="Checking"))
    db.session.commit()
    html = client.get("/jobs/help-job/fragment").get_data(as_text=True)
    assert ('data-assistant-help' in html) is expected
    if expected:
        assert 'data-job-id="help-job"' in html
        assert 'class="btn btn-secondary btn-sm" data-icon="assistant" data-assistant-help' in html
        assert "Ask Yaffo" in html


def test_job_help_is_shown_for_partial_errors(client, with_key):
    db.session.add(Job(id="partial-error", name="index_photos", status="COMPLETED",
                       task_count=3, completed_count=2, error_count=1, cancelled_count=0, message="Finished"))
    db.session.commit()
    body = client.get("/jobs/partial-error/fragment").get_data(as_text=True)
    assert 'data-assistant-help' in body


@pytest.fixture
def opened(monkeypatch):
    """Record what the open route would hand the OS instead of opening it."""
    calls = []
    monkeypatch.setattr("yaffo.routes.assistant.open_in_os", lambda path, reveal=False: calls.append((path, reveal)))
    return calls


@pytest.fixture
def media_root(tmp_path, monkeypatch):
    from types import SimpleNamespace
    root = tmp_path / "Photos"
    (root / "2019").mkdir(parents=True)
    (root / "2019" / "a.jpg").write_bytes(b"x")
    monkeypatch.setattr("yaffo.site_agents.assistant.file_targets.get_media_dir_entries",
                        lambda session: [SimpleNamespace(id="m1", path=root)])
    return root


def test_open_button_opens_the_looked_up_path(client, opened, media_root):
    photo = media_root / "2019" / "a.jpg"
    item = MediaItem(full_file_path=str(photo))
    db.session.add(item)
    db.session.commit()

    assert client.post("/api/assistant/open", json={"media_item_id": item.id, "show": "folder"}).status_code == 204
    assert client.post("/api/assistant/open", json={"media_dir_id": "m1", "path": "2019", "show": "file"}).status_code == 204
    # A file shown "in its folder" is revealed; a folder just opens.
    assert opened == [(photo.resolve(), True), ((media_root / "2019").resolve(), False)]


@pytest.mark.parametrize("body", [
    {"media_dir_id": "m1", "path": "../..", "show": "file"},
    {"media_dir_id": "m1", "path": "/etc", "show": "file"},
    {"path": "/etc/passwd", "show": "file"},
    {"media_dir_id": "m1", "path": "missing.jpg", "show": "file"},
    {"media_item_id": 424242, "show": "file"},
    "not an object",
])
def test_open_button_refuses_anything_else(client, opened, media_root, body):
    response = client.post("/api/assistant/open", json=body)
    assert response.status_code == 404
    assert response.get_json()["code"] == "open_target_unavailable"
    assert opened == []


def test_open_button_reports_when_the_os_cannot_open(client, media_root, monkeypatch):
    def fail(path, reveal=False):
        raise OSError("no handler")
    monkeypatch.setattr("yaffo.routes.assistant.open_in_os", fail)
    response = client.post("/api/assistant/open", json={"media_dir_id": "m1", "path": "", "show": "file"})
    assert response.status_code == 500 and response.get_json()["code"] == "open_failed"
