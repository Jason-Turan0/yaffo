"""Route tests for the AI page builder (yaffo/routes/pages.py).

These exercise the HTTP surface end to end against the wired repository: status
codes, redirects, 404s, and the database side-effects each route commits. Stubbed
data resolution (resolve_data / resolve_query) and the model are not asserted on
for content -- the chat route's generation path is driven by a fake agent.
"""
import json

import pytest

from yaffo.db import db
from yaffo.db.models import Conversation, Widget
from yaffo.db.repositories import custom_page_repository as page_repo
from yaffo.page_builder.agent import AgentEvent

pytestmark = pytest.mark.unit


# --- helpers ---------------------------------------------------------------

def _make_page(title="Trip", theme_prompt=""):
    """Seed a page directly through the repo; return its id."""
    return page_repo.create_page(db.session, title=title, theme_prompt=theme_prompt).id


def _save_widget(page_id, wid="w1", **over):
    item = {"id": wid, "title": "W", "html": "<div class='probe'></div>",
            "js": "/*probe*/", "x": 0, "y": 0, "w": 4, "h": 3}
    item.update(over)
    page_repo.save_page_widgets(db.session, page_id, [item])


def _records(resp):
    """Parse a newline-delimited-JSON stream response into a list of records."""
    db.session.expire_all()  # the stream committed in its own request context
    return [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line.strip()]


def _reload_page(page_id):
    db.session.expire_all()
    return page_repo.get_page(db.session, page_id)


# --- POST /pages -----------------------------------------------------------

class TestCreatePage:
    def test_redirects_to_detail_and_persists(self, client):
        resp = client.post("/pages", data={"title": "My Trip", "theme_prompt": "summer"})
        assert resp.status_code == 302
        page_id = int(resp.headers["Location"].rstrip("/").split("/")[-1])
        page = _reload_page(page_id)
        assert page.title == "My Trip"
        assert page.theme_prompt == "summer"

    def test_blank_title_defaults(self, client):
        resp = client.post("/pages", data={"title": "   "})
        page_id = int(resp.headers["Location"].rstrip("/").split("/")[-1])
        assert _reload_page(page_id).title == "Untitled Page"


# --- GET /pages/<id> -------------------------------------------------------

class TestDetail:
    def test_empty_page_redirects_to_design(self, client):
        pid = _make_page()
        resp = client.get(f"/pages/{pid}")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/pages/{pid}/design")

    def test_nonempty_page_renders_presentation(self, client):
        pid = _make_page(title="Shown")
        _save_widget(pid)
        resp = client.get(f"/pages/{pid}")
        assert resp.status_code == 200
        assert "Shown" in resp.get_data(as_text=True)

    def test_unknown_page_404(self, client):
        assert client.get("/pages/999").status_code == 404


# --- GET /pages/<id>/design ------------------------------------------------

class TestDesign:
    def test_renders(self, client):
        pid = _make_page()
        assert client.get(f"/pages/{pid}/design").status_code == 200

    def test_unknown_page_404(self, client):
        assert client.get("/pages/999/design").status_code == 404


# --- POST /pages/<id>/update -----------------------------------------------

class TestUpdate:
    def test_persists_fields_and_widgets(self, client):
        pid = _make_page()
        resp = client.post(f"/pages/{pid}/update", json={
            "title": "Renamed",
            "theme_prompt": "maine",
            "show_title": False,
            "widgets": [{"id": "w1", "title": "Wall", "html": "<div>",
                         "x": 1, "y": 2, "w": 6, "h": 4}],
        })
        assert resp.status_code == 204
        page = _reload_page(pid)
        assert page.title == "Renamed"
        assert page.theme_prompt == "maine"
        assert page.show_title is False
        assert [(w.id, w.title, w.grid_w) for w in page.widgets] == [("w1", "Wall", 6)]

    def test_blank_title_defaults(self, client):
        pid = _make_page(title="Original")
        client.post(f"/pages/{pid}/update", json={"title": "  ", "widgets": []})
        assert _reload_page(pid).title == "Untitled Page"

    def test_unknown_page_404(self, client):
        assert client.post("/pages/999/update", json={"title": "x"}).status_code == 404


# --- POST /pages/<id>/delete -----------------------------------------------

class TestDelete:
    def test_deletes_and_redirects(self, client):
        pid = _make_page()
        resp = client.post(f"/pages/{pid}/delete")
        assert resp.status_code == 302
        assert _reload_page(pid) is None


# --- GET /pages/<id>/widgets/<wid>/frame -----------------------------------

class TestWidgetFrame:
    def test_renders_with_csp_and_widget_code(self, client):
        pid = _make_page()
        _save_widget(pid)
        resp = client.get(f"/pages/{pid}/widgets/w1/frame")
        assert resp.status_code == 200
        assert "connect-src 'none'" in resp.headers["Content-Security-Policy"]
        body = resp.get_data(as_text=True)
        assert "probe" in body  # widget html + js were injected

    def test_unknown_widget_404(self, client):
        pid = _make_page()
        assert client.get(f"/pages/{pid}/widgets/nope/frame").status_code == 404

    def test_unknown_page_404(self, client):
        assert client.get("/pages/999/widgets/w1/frame").status_code == 404


# --- POST /pages/<id>/widgets/preview --------------------------------------

class TestWidgetPreview:
    def test_renders_grid_item_without_persisting(self, client):
        pid = _make_page()
        resp = client.post(f"/pages/{pid}/widgets/preview", json={
            "id": "draft1", "title": "Draft", "html": "<div class='probe'></div>",
        })
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'gs-id="draft1"' in body
        # Preview must not write -- Save is the only writer.
        db.session.expire_all()
        assert db.session.query(Widget).count() == 0

    def test_mints_id_when_absent(self, client):
        pid = _make_page()
        resp = client.post(f"/pages/{pid}/widgets/preview", json={"title": "Draft"})
        assert resp.status_code == 200
        assert 'gs-id=""' not in resp.get_data(as_text=True)

    def test_unknown_page_404(self, client):
        assert client.post("/pages/999/widgets/preview", json={}).status_code == 404


# --- POST /pages/<id>/widgets/<wid>/delete ---------------------------------

class TestDeleteWidget:
    def test_removes_widget(self, client):
        pid = _make_page()
        _save_widget(pid)
        resp = client.post(f"/pages/{pid}/widgets/w1/delete")
        assert resp.status_code == 204
        assert _reload_page(pid).widgets == []

    def test_missing_widget_is_noop_204(self, client):
        pid = _make_page()
        assert client.post(f"/pages/{pid}/widgets/nope/delete").status_code == 204


# --- POST /pages/<id>/widgets/<wid>/query ----------------------------------

class TestWidgetQuery:
    def test_returns_resolved_data(self, client):
        pid = _make_page()
        _save_widget(pid)
        # A valid query against a real source; the test DB has no photos, so rows
        # come back empty — the point is the route resolves and returns data.
        resp = client.post(f"/pages/{pid}/widgets/w1/query", json={"query": {"source": "photos", "limit": 5}})
        assert resp.status_code == 200
        assert resp.get_json()["data"] == []

    def test_invalid_query_fails_closed(self, client):
        pid = _make_page()
        _save_widget(pid)
        resp = client.post(f"/pages/{pid}/widgets/w1/query", json={"query": {"source": "bogus"}})
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None

    def test_unknown_widget_404(self, client):
        pid = _make_page()
        assert client.post(f"/pages/{pid}/widgets/nope/query", json={"query": {}}).status_code == 404


# --- POST /pages/<id>/widgets/<wid>/state ----------------------------------

class TestWidgetState:
    def test_persists_state(self, client):
        pid = _make_page()
        _save_widget(pid)
        resp = client.post(f"/pages/{pid}/widgets/w1/state", json={"state": {"filter": "Camden"}})
        assert resp.status_code == 204
        db.session.expire_all()
        assert page_repo.get_widget(db.session, pid, "w1").state == {"filter": "Camden"}

    def test_unknown_widget_404(self, client):
        pid = _make_page()
        assert client.post(f"/pages/{pid}/widgets/nope/state", json={"state": {}}).status_code == 404


# --- POST /pages/<id>/chat : guard paths (no API key) ----------------------

class TestChatGuards:
    def test_empty_message_streams_only_done(self, client):
        pid = _make_page()
        resp = client.post(f"/pages/{pid}/chat", json={"message": "  "})
        assert resp.status_code == 200
        assert _records(resp) == [{"event": "done"}]

    def test_no_api_key_message_and_persists_user_turn(self, client):
        pid = _make_page()
        resp = client.post(f"/pages/{pid}/chat", json={"message": "build a gallery"})
        events = _records(resp)
        assert events[-1] == {"event": "done"}
        assert any(e["event"] == "message" and "API key" in e["content"] for e in events)
        # The user turn and the assistant's no-key reply are both persisted.
        roles = [(m.role, m.content) for m in _reload_page(pid).messages]
        assert roles[0] == ("user", "build a gallery")
        assert any(role == "assistant" and "API key" in content for role, content in roles)

    def test_unknown_page_404(self, client):
        assert client.post("/pages/999/chat", json={"message": "hi"}).status_code == 404


# --- POST /pages/<id>/chat : mocked generation happy path ------------------

class _FakeAgent:
    def __init__(self, events):
        self._events = events

    def run_events(self, user_message):
        yield from self._events


class TestChatGeneration:
    @pytest.fixture
    def with_agent(self, monkeypatch):
        """Pretend a key is configured and swap the real agent for one that emits a
        canned assistant turn + a create_widget tool result + done."""
        monkeypatch.setattr("yaffo.page_builder.llm_config.get_api_key", lambda: "test-key")
        widget = {"id": "g1", "title": "Gallery", "html": "<div>", "css": "", "js": "",
                  "data_query": {}, "grid_w": 4, "grid_h": 3}
        events = [
            AgentEvent("assistant", text="Here is a gallery."),
            AgentEvent("tool", name="create_widget", tool_result_data=widget),
            AgentEvent("done"),
        ]
        monkeypatch.setattr("yaffo.routes.pages.create_agent",
                            lambda *a, **k: _FakeAgent(events))
        return widget

    def test_streams_message_status_widget_and_done(self, client, with_agent):
        pid = _make_page()
        resp = client.post(f"/pages/{pid}/chat", json={"message": "make a gallery", "widgets": []})
        events = _records(resp)
        kinds = [e["event"] for e in events]
        assert kinds == ["message", "status", "widget_new", "done"]
        assert events[2]["widget"] == with_agent

    def test_persists_messages_but_not_widgets(self, client, with_agent):
        pid = _make_page()
        client.post(f"/pages/{pid}/chat", json={"message": "make a gallery", "widgets": []})
        roles = [(m.role, m.content) for m in _reload_page(pid).messages]
        assert ("user", "make a gallery") in roles
        assert ("assistant", "Here is a gallery.") in roles
        # Generation is non-destructive: the streamed widget is a draft, unsaved.
        assert db.session.query(Widget).count() == 0