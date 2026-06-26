"""Route tests for the AI page builder (yaffo/routes/pages.py).

These exercise the HTTP surface end to end against the wired repository: status
codes, redirects, 404s, and the database side-effects each route commits. Stubbed
data resolution (resolve_data / resolve_query) and the model are not asserted on
for content -- the chat route's generation path is driven by a fake agent.
"""
import pytest

from yaffo.db import db
from yaffo.db.models import (
    PAGE_VERSION_STATUS_IN_PROGRESS,
    PAGE_VERSION_STATUS_READY,
    Widget,
)
from yaffo.db.repositories import custom_page_repository as page_repo

pytestmark = pytest.mark.unit


# --- helpers ---------------------------------------------------------------

def _make_page(title="Trip", subtitle=""):
    """Seed a page directly through the repo; return its id."""
    return page_repo.create_page(db.session, title=title, subtitle=subtitle).id


def _save_widget(page_id, wid="w1", **over):
    item = {"id": wid, "title": "W", "html": "<div class='probe'></div>",
            "js": "/*probe*/", "x": 0, "y": 0, "w": 4, "h": 3}
    item.update(over)
    page_repo.save_page_widgets(db.session, page_id, [item])


def _reload_page(page_id):
    db.session.expire_all()
    return page_repo.get_page(db.session, page_id)


# --- POST /pages -----------------------------------------------------------

class TestCreatePage:
    def test_redirects_to_detail_and_persists(self, client):
        resp = client.post("/pages", data={"title": "My Trip", "page_subtitle": "summer"})
        assert resp.status_code == 302
        page_id = int(resp.headers["Location"].rstrip("/").split("/")[-1])
        page = _reload_page(page_id)
        assert page.title == "My Trip"
        assert page.subtitle == "summer"

    def test_blank_title_defaults(self, client):
        resp = client.post("/pages", data={"title": "   "})
        page_id = int(resp.headers["Location"].rstrip("/").split("/")[-1])
        assert _reload_page(page_id).title == "Untitled Page"

    def test_blank_title_default_uses_saved_locale(self, client):
        client.post("/settings/locale", data={"locale": "de"})
        resp = client.post("/pages", data={"title": "   "})
        page_id = int(resp.headers["Location"].rstrip("/").split("/")[-1])
        assert _reload_page(page_id).title == "Unbenannte Seite"


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

    def test_saved_locale_translates_widget_grid_chrome(self, client):
        pid = _make_page(title="Shown")
        _save_widget(pid, title="Karte")
        client.post("/settings/locale", data={"locale": "de"})

        resp = client.get(f"/pages/{pid}/design")

        body = resp.get_data(as_text=True)
        assert ">Titel<" in body
        assert ">Untertitel<" in body
        assert ">Tab-Reihenfolge<" in body
        assert "Titel anzeigen?" in body
        assert ">Widget hinzufügen<" in body
        assert ">Speichern<" in body
        assert ">Seite löschen<" in body
        assert "Assistent" in body
        assert "Gemeinsam Ihre Seite erstellen." in body
        assert "Beschreiben Sie, was Sie möchten…" in body
        assert 'aria-label="Widget-Titel"' in body
        assert 'aria-label="Titel bearbeiten"' in body
        assert 'aria-label="Widget löschen"' in body
        assert 'title="Karte Vorschau"' in body
        assert "window.PHOTO_ORGANIZER.i18nReady.then((i18n)" in body
        assert "onclick=" not in body

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
            "subtitle": "maine",
            "show_title": False,
            "widgets": [{"id": "w1", "title": "Wall", "html": "<div>",
                         "x": 1, "y": 2, "w": 6, "h": 4}],
        })
        assert resp.status_code == 204
        page = _reload_page(pid)
        assert page.title == "Renamed"
        assert page.subtitle == "maine"
        assert page.show_title is False
        assert [(w.id, w.title, w.grid_w) for w in page.widgets] == [("w1", "Wall", 6)]

    def test_tab_order_displaces_others(self, client):
        # Three pages at positions 1, 2, 3; move the first to position 3.
        a, b, c = _make_page(title="A"), _make_page(title="B"), _make_page(title="C")
        resp = client.post(f"/pages/{a}/update", json={"title": "A", "tab_order": 3, "widgets": []})
        assert resp.status_code == 204
        order = {p.title: p.tab_order for p in page_repo.list_pages(db.session)}
        assert order == {"B": 1, "C": 2, "A": 3}

    def test_blank_title_defaults(self, client):
        pid = _make_page(title="Original")
        client.post(f"/pages/{pid}/update", json={"title": "  ", "widgets": []})
        assert _reload_page(pid).title == "Untitled Page"

    def test_blank_title_default_uses_saved_locale(self, client):
        pid = _make_page(title="Original")
        client.post("/settings/locale", data={"locale": "de"})
        client.post(f"/pages/{pid}/update", json={"title": "  ", "widgets": []})
        assert _reload_page(pid).title == "Unbenannte Seite"

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

    def test_saved_locale_translates_runtime_error_label(self, client):
        pid = _make_page()
        _save_widget(pid)
        client.post("/settings/locale", data={"locale": "de"})
        resp = client.get(f"/pages/{pid}/widgets/w1/frame")
        body = resp.get_data(as_text=True)
        assert 'lang="de"' in body
        assert "Dieses Widget konnte nicht dargestellt werden" in body
        assert "This widget couldn't render" not in body


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


# --- POST /pages/<id>/versions/<vid>/widgets/<wid>/delete ------------------

class TestDeleteWidget:
    def test_removes_widget_from_the_given_version(self, client):
        pid = _make_page()
        _save_widget(pid)  # into the published version
        vid = _reload_page(pid).published_version_id
        resp = client.post(f"/pages/{pid}/versions/{vid}/widgets/w1/delete")
        assert resp.status_code == 204
        assert _reload_page(pid).widgets == []

    def test_missing_widget_is_noop_204(self, client):
        pid = _make_page()
        vid = _reload_page(pid).published_version_id
        assert client.post(f"/pages/{pid}/versions/{vid}/widgets/nope/delete").status_code == 204

    def test_404_when_version_not_on_page(self, client):
        pid = _make_page()
        other_vid = _reload_page(_make_page()).published_version_id
        assert client.post(f"/pages/{pid}/versions/{other_vid}/widgets/x/delete").status_code == 404

    def test_deleting_from_a_draft_leaves_the_published_page_untouched(self, client):
        # The crux of editing an explicit version: with a draft open, deleting a
        # widget hits the draft, not the live page (both hold the id after a fork).
        pid = _make_page()
        _save_widget(pid, "shared")
        working = page_repo.fork_version(db.session, pid).id
        resp = client.post(f"/pages/{pid}/versions/{working}/widgets/shared/delete")
        assert resp.status_code == 204
        assert page_repo.get_version_widget(db.session, working, "shared") is None
        assert [w.id for w in _reload_page(pid).widgets] == ["shared"]  # published intact


# --- POST /pages/<id>/versions/<vid>/widgets/<wid>/query --------------------

class TestWidgetQuery:
    def test_returns_resolved_data(self, client):
        pid = _make_page()
        _save_widget(pid)
        vid = _reload_page(pid).published_version_id
        # A valid query against a real source; the test DB has no photos, so rows
        # come back empty — the point is the route resolves and returns data.
        resp = client.post(f"/pages/{pid}/versions/{vid}/widgets/w1/query", json={"query": {"source": "media_items", "limit": 5}})
        assert resp.status_code == 200
        assert resp.get_json()["data"] == []

    def test_invalid_query_fails_closed(self, client):
        pid = _make_page()
        _save_widget(pid)
        vid = _reload_page(pid).published_version_id
        resp = client.post(f"/pages/{pid}/versions/{vid}/widgets/w1/query", json={"query": {"source": "bogus"}})
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None

    def test_unknown_widget_404(self, client):
        pid = _make_page()
        vid = _reload_page(pid).published_version_id
        assert client.post(f"/pages/{pid}/versions/{vid}/widgets/nope/query", json={"query": {}}).status_code == 404


# --- POST /pages/<id>/versions/<vid>/widgets/<wid>/state --------------------

class TestWidgetState:
    def test_persists_state_on_the_given_version(self, client):
        pid = _make_page()
        _save_widget(pid)
        vid = _reload_page(pid).published_version_id
        resp = client.post(f"/pages/{pid}/versions/{vid}/widgets/w1/state", json={"state": {"filter": "Camden"}})
        assert resp.status_code == 204
        db.session.expire_all()
        assert page_repo.get_version_widget(db.session, vid, "w1").state == {"filter": "Camden"}

    def test_draft_state_does_not_leak_to_the_published_widget(self, client):
        # State written against a draft's widget stays on the draft.
        pid = _make_page()
        _save_widget(pid, "shared")
        working = page_repo.fork_version(db.session, pid).id
        client.post(f"/pages/{pid}/versions/{working}/widgets/shared/state", json={"state": {"k": 1}})
        db.session.expire_all()
        assert page_repo.get_version_widget(db.session, working, "shared").state == {"k": 1}
        assert page_repo.get_widget(db.session, pid, "shared").state == {}  # published untouched

    def test_unknown_widget_404(self, client):
        pid = _make_page()
        vid = _reload_page(pid).published_version_id
        assert client.post(f"/pages/{pid}/versions/{vid}/widgets/nope/state", json={"state": {}}).status_code == 404


# --- POST /pages/<id>/chat : enqueue async generation ----------------------

@pytest.fixture
def with_key_and_task(monkeypatch):
    """Configure an API key and capture enqueued generation tasks instead of
    handing them to the task queue, so the route's fork + enqueue is exercised without a
    worker. Returns the list of (args, kwargs) the task was called with."""
    monkeypatch.setattr("yaffo.site_agents.llm_config.get_api_key", lambda *a, **k: "test-key")
    calls = []
    monkeypatch.setattr("yaffo.routes.pages.generate_page_task",
                        lambda *a, **k: calls.append((a, k)))
    return calls


class TestChatGuards:
    def test_empty_message_is_400_and_does_not_fork(self, client):
        pid = _make_page()
        resp = client.post(f"/pages/{pid}/chat", json={"message": "  "})
        assert resp.status_code == 400
        assert resp.get_json() == {"error": "Message is required.", "code": "message_required"}
        assert _reload_page(pid).working_version_id is None

    def test_empty_message_uses_saved_locale_and_error_code(self, client):
        pid = _make_page()
        client.post("/settings/locale", data={"locale": "de"})
        resp = client.post(f"/pages/{pid}/chat", json={"message": "  "})
        assert resp.status_code == 400
        assert resp.get_json() == {"error": "Nachricht ist erforderlich.", "code": "message_required"}
        assert _reload_page(pid).working_version_id is None

    def test_no_api_key_is_400_and_does_not_fork(self, client):
        # The autouse no_api_key fixture leaves generation disabled.
        pid = _make_page()
        resp = client.post(f"/pages/{pid}/chat", json={"message": "build a gallery"})
        assert resp.status_code == 400
        assert "API key" in resp.get_json()["error"]
        assert resp.get_json()["code"] == "api_key_missing"
        assert _reload_page(pid).working_version_id is None

    def test_unknown_page_404(self, client):
        assert client.post("/pages/999/chat", json={"message": "hi"}).status_code == 404


class TestChatEnqueue:
    def test_forks_version_persists_user_turn_and_enqueues(self, client, with_key_and_task):
        pid = _make_page()
        resp = client.post(f"/pages/{pid}/chat", json={"message": "make a gallery", "widgets": []})
        assert resp.status_code == 202
        version_id = resp.get_json()["version_id"]

        page = _reload_page(pid)
        assert page.working_version_id == version_id  # UI is locked to the new version
        # The user turn lives on the working version, not the published feed.
        version = page_repo.get_version(db.session, version_id)
        assert [(m.type, m.content) for m in version.messages] == [("user", "make a gallery")]
        # The background task was enqueued with (version_id, message, widget_errors).
        (args, _kwargs) = with_key_and_task[0]
        assert args[0] == version_id
        assert args[1] == "make a gallery"

    def test_seeds_working_version_from_published_widgets_when_omitted(self, client, with_key_and_task):
        pid = _make_page()
        _save_widget(pid, "w1", title="Saved")
        resp = client.post(f"/pages/{pid}/chat", json={"message": "tweak it"})  # widgets omitted
        version_id = resp.get_json()["version_id"]
        version = page_repo.get_version(db.session, version_id)
        assert [(w.id, w.title) for w in version.widgets] == [("w1", "Saved")]

    def test_no_working_version_means_no_enqueue_on_guard(self, client):
        # Guard paths must not enqueue (no key here via the autouse fixture).
        pid = _make_page()
        client.post(f"/pages/{pid}/chat", json={"message": "hi"})
        assert _reload_page(pid).working_version_id is None

    def test_follow_up_continues_on_the_same_working_version(self, client, with_key_and_task):
        pid = _make_page()
        v1 = client.post(f"/pages/{pid}/chat", json={"message": "build", "widgets": []}).get_json()["version_id"]
        # Simulate the run finishing (the task is stubbed); the draft is now in review.
        page_repo.set_version_status(db.session, v1, PAGE_VERSION_STATUS_READY, completed=True)

        resp = client.post(f"/pages/{pid}/chat", json={"message": "make it bigger", "widgets": []})
        assert resp.status_code == 202
        assert resp.get_json()["version_id"] == v1  # same version, no new fork

        version = page_repo.get_version(db.session, v1)
        assert version.status == PAGE_VERSION_STATUS_IN_PROGRESS  # restarted
        assert [m.content for m in version.messages if m.type == "user"] == ["build", "make it bigger"]

    def test_rejects_a_send_while_a_generation_is_running(self, client, with_key_and_task):
        pid = _make_page()
        client.post(f"/pages/{pid}/chat", json={"message": "build", "widgets": []})  # leaves it IN_PROGRESS
        resp = client.post(f"/pages/{pid}/chat", json={"message": "again"})
        assert resp.status_code == 409
        assert resp.get_json() == {"error": "A generation is already running.", "code": "generation_already_running"}

    def test_follow_up_commits_client_widget_moves(self, client, with_key_and_task):
        pid = _make_page()
        v1 = client.post(f"/pages/{pid}/chat", json={"message": "build", "widgets": []}).get_json()["version_id"]
        page_repo.set_version_status(db.session, v1, PAGE_VERSION_STATUS_READY, completed=True)
        client.post(f"/pages/{pid}/chat", json={"message": "more", "widgets": [
            {"id": "m1", "title": "Moved", "html": "<p>", "x": 5, "y": 5, "w": 4, "h": 3},
        ]})
        widget = page_repo.get_version_widget(db.session, v1, "m1")
        assert (widget.grid_x, widget.grid_y) == (5, 5)


# --- GET /pages/<id>/versions/<vid>/status ---------------------------------

class TestVersionStatus:
    def test_returns_status_feed_and_widgets(self, client):
        pid = _make_page()
        vid = page_repo.fork_version(db.session, pid).id
        page_repo.add_version_message(db.session, vid, "status", "Creating widget…")
        page_repo.upsert_version_widget(db.session, vid, {
            "id": "g1", "title": "G", "html": "<div>", "data_query": {}, "grid_w": 4, "grid_h": 3,
        })
        resp = client.get(f"/pages/{pid}/versions/{vid}/status")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == PAGE_VERSION_STATUS_IN_PROGRESS
        assert {"type": "status", "content": "Creating widget…"} in body["messages"]
        assert [w["id"] for w in body["widgets"]] == ["g1"]

    def test_404_when_version_belongs_to_another_page(self, client):
        pid = _make_page()
        other = _make_page()
        vid = page_repo.fork_version(db.session, other).id
        assert client.get(f"/pages/{pid}/versions/{vid}/status").status_code == 404

    def test_404_missing_version(self, client):
        pid = _make_page()
        assert client.get(f"/pages/{pid}/versions/9999/status").status_code == 404


# --- POST /pages/<id>/versions/<vid>/cancel --------------------------------

class TestVersionCancel:
    def test_deletes_version_and_reverts_to_published(self, client):
        pid = _make_page()
        published_before = _reload_page(pid).published_version_id
        vid = page_repo.fork_version(db.session, pid).id
        resp = client.post(f"/pages/{pid}/versions/{vid}/cancel")
        assert resp.status_code == 204
        page = _reload_page(pid)
        assert page.working_version_id is None
        assert page.published_version_id == published_before
        assert page_repo.get_version(db.session, vid) is None

    def test_404_unknown(self, client):
        pid = _make_page()
        assert client.post(f"/pages/{pid}/versions/9999/cancel").status_code == 404


# --- POST /pages/<id>/versions/<vid>/publish -------------------------------

class TestVersionPublish:
    def test_publishes_a_ready_version(self, client):
        pid = _make_page()
        vid = page_repo.fork_version(db.session, pid, widgets=[
            {"id": "z", "title": "Z", "x": 0, "y": 0, "w": 4, "h": 3},
        ]).id
        page_repo.set_version_status(db.session, vid, PAGE_VERSION_STATUS_READY, completed=True)
        resp = client.post(f"/pages/{pid}/versions/{vid}/publish")
        assert resp.status_code == 204
        page = _reload_page(pid)
        assert page.published_version_id == vid
        assert page.working_version_id is None
        assert [w.id for w in page.widgets] == ["z"]

    def test_409_when_not_ready(self, client):
        pid = _make_page()
        vid = page_repo.fork_version(db.session, pid).id  # still IN_PROGRESS
        resp = client.post(f"/pages/{pid}/versions/{vid}/publish")
        assert resp.status_code == 409
        assert resp.get_json() == {"error": "Version is not ready to publish.", "code": "version_not_ready"}
        assert _reload_page(pid).published_version_id != vid

    def test_not_ready_error_uses_saved_locale_and_error_code(self, client):
        pid = _make_page()
        vid = page_repo.fork_version(db.session, pid).id
        client.post("/settings/locale", data={"locale": "de"})
        resp = client.post(f"/pages/{pid}/versions/{vid}/publish")
        assert resp.status_code == 409
        assert resp.get_json() == {"error": "Version ist noch nicht bereit zur Veröffentlichung.", "code": "version_not_ready"}
        assert _reload_page(pid).published_version_id != vid

    def test_commits_client_widget_moves_before_publishing(self, client):
        pid = _make_page()
        vid = page_repo.fork_version(db.session, pid, widgets=[
            {"id": "z", "title": "Z", "x": 0, "y": 0, "w": 4, "h": 3},
        ]).id
        page_repo.set_version_status(db.session, vid, PAGE_VERSION_STATUS_READY, completed=True)
        resp = client.post(f"/pages/{pid}/versions/{vid}/publish", json={"widgets": [
            {"id": "z", "x": 7, "y": 2, "w": 4, "h": 3},  # moved during review
        ]})
        assert resp.status_code == 204
        widget = _reload_page(pid).widgets[0]
        assert (widget.id, widget.grid_x, widget.grid_y) == ("z", 7, 2)

    def test_404_unknown(self, client):
        pid = _make_page()
        assert client.post(f"/pages/{pid}/versions/9999/publish").status_code == 404
