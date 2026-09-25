"""Routes for Ask Yaffo. The background run is replaced by a recorder, so these
cover the HTTP contract: starting turns, polling, cancel, rename/delete, the
settings switches, and availability (setting and demo mode)."""
import pytest

from yaffo.db import db
from yaffo.db.models import ASSISTANT_STATUS_FAILED, ASSISTANT_STATUS_IDLE, ASSISTANT_STATUS_RUNNING
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
