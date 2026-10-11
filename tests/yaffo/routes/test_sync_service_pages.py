"""Route tests for the service builder: Add a new service…, the builder page and its
tabs, the chat, the test account (a key at a self-hosted server, and an OAuth app),
the user's Test, Publish, Regenerate and Delete."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from yaffo.db import db
from yaffo.db.models import (
    AUTOMATION_STATUS_IN_PROGRESS, ApplicationSettings, CONVERSATION_TYPE_TEST, CONVERSATION_TYPE_USER, SyncAdapter,
)
from yaffo.db.repositories import service_builder_repository as services
from yaffo.routes.utilities import sync_services
from yaffo.sync import connections, secrets_store

pytestmark = pytest.mark.unit

PRESETS = Path(__file__).parents[3] / "yaffo" / "sync" / "presets"
IMMICH_CONNECTION = ('"provider": "custom",\n    "connection": {"auth_kind": "api_key", "instance": ["base_url"], '
                     '"check": {"method": "GET", "url": "{base_url}/api/users/me", "account_label": ["email"]}, '
                     '"api_key": {"in": "header", "name": "x-api-key"}, "key_label": "Immich API key"},\n')


@pytest.fixture(autouse=True)
def secrets_file(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "secrets.json"
    monkeypatch.setenv("YAFFO_SYNC_SECRETS_FILE", str(path))
    return path


@pytest.fixture
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr(sync_services, "generate_adapter_task", lambda adapter_id, message: calls.append(message))
    monkeypatch.setattr("yaffo.site_agents.llm_config.selected_key_missing", lambda *a, **k: False)
    return calls


@pytest.fixture
def immich():
    from scripts.fake_immich import FakeImmich

    with FakeImmich(photos=3, album="Family") as server:
        yield server


def custom_immich(kind="source") -> str:
    return (PRESETS / f"immich_{kind}.star").read_text().replace('"provider": "immich",\n', IMMICH_CONNECTION)


def new_service(client, name="My Immich") -> SyncAdapter:
    response = client.post("/utilities/automations/create", data={"name": name, "kind": "service"})
    assert response.status_code == 302
    row = db.session.query(SyncAdapter).filter(SyncAdapter.name == name).one()
    assert response.headers["Location"].endswith(f"/utilities/automations/services/{row.slug}")
    return row


def page(client, row, tab="overview") -> str:
    response = client.get(f"/utilities/automations/services/{row.slug}?tab={tab}")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_add_a_new_service_opens_an_empty_builder(client, app):
    assert "Add a new service…" in client.get("/utilities/automations").get_data(as_text=True)
    row = new_service(client)

    html = page(client, row)

    assert 'id="service-chat"' in html and "Nothing yet. The builder fills this in" in html
    assert "Draft" in html and 'id="service-publish"' not in html


def test_the_chat_starts_the_builder_once_at_a_time(client, app, queued):
    row = new_service(client)

    first = client.post(f"/utilities/automations/services/{row.slug}/chat", json={"message": "Import from my Immich"})
    second = client.post(f"/utilities/automations/services/{row.slug}/chat", json={"message": "again"})

    assert first.status_code == 202 and second.status_code == 409 and queued == ["Import from my Immich"]
    status = client.get(f"/utilities/automations/services/{row.slug}/status").get_json()
    assert status["status"] == AUTOMATION_STATUS_IN_PROGRESS
    assert [(m["type"], m["content"]) for m in status["messages"]] == [(CONVERSATION_TYPE_USER, "Import from my Immich")]
    client.post(f"/utilities/automations/services/{row.slug}/cancel")
    assert client.get(f"/utilities/automations/services/{row.slug}/status").get_json()["status"] == "READY"


def test_the_tabs_show_the_draft(client, app):
    row = new_service(client)
    services.save_draft(db.session, row.id, custom_immich())

    overview = page(client, row)
    web = page(client, row, "web")
    code = page(client, row, "code")

    assert "Imports photos" in overview and "Your server" in overview
    assert "<dd>API key</dd>" in overview and 'id="service-publish"' in overview
    assert "{base_url}/api/search/metadata" in web and web.count(">Reads<") == 2
    assert "def list_page" in code


def test_a_failing_draft_lists_its_problems(client, app):
    row = new_service(client)
    services.save_draft(db.session, row.id, "ADAPTER = 1")

    html = page(client, row)

    assert "doesn't pass its check yet" in html and "must be a dict literal" in html and 'id="service-publish"' not in html


def test_the_test_account_and_a_live_test(client, app, immich):
    row = new_service(client)
    services.save_draft(db.session, row.id, custom_immich())
    assert "Immich API key" in page(client, row, "account")

    response = client.post(f"/utilities/automations/services/{row.slug}/test-account",
                           data={"base_url": immich.base_url, "allow_http": "1", "api_key": "immich-test-key"})

    assert "service_error" not in response.headers["Location"]
    db.session.refresh(row)
    assert connections.get(db.session, row.test_connection_key).instance == {"base_url": immich.base_url}
    assert "jason@example.com" in page(client, row, "account")

    result = client.post(f"/utilities/automations/services/{row.slug}/test", data={"option_album": "album-1"})
    html = result.get_data(as_text=True)
    assert "Passed" in html and "Read · 200" in html and "album-1" in html and "First page: 3 records" in html
    tests = [m for m in services.messages(db.session, row.id) if m.type == CONVERSATION_TYPE_TEST]
    assert tests and "live reads" in tests[-1].content


def test_a_wrong_key_for_the_test_account_is_reported(client, app, immich):
    row = new_service(client)
    services.save_draft(db.session, row.id, custom_immich())

    response = client.post(f"/utilities/automations/services/{row.slug}/test-account",
                           data={"base_url": immich.base_url, "allow_http": "1", "api_key": "wrong"})

    assert response.headers["Location"].endswith("service_error=rejected")
    assert "refused that key" in client.get(response.headers["Location"]).get_data(as_text=True)


def test_an_oauth_service_needs_the_users_own_app_first(client, app):
    row = new_service(client, "Dropbox")
    code = custom_immich().replace(IMMICH_CONNECTION, '''"provider": "custom",
    "connection": {"auth_kind": "oauth2", "base_urls": ["https://api.dropboxapi.com"], "redirect": "registered",
                   "auth": {"authorize_url": "https://www.dropbox.com/oauth2/authorize",
                            "token_url": "https://api.dropboxapi.com/oauth2/token", "scopes": ["files.content.read"]},
                   "app_credentials": ["client_id", "client_secret"],
                   "check": {"method": "GET", "url": "https://api.dropboxapi.com/2/users/get_current_account"}},
''').replace("{base_url}", "https://api.dropboxapi.com")
    assert services.save_draft(db.session, row.id, code).ok

    refused = client.post(f"/utilities/automations/services/{row.slug}/test-account")
    assert refused.headers["Location"].endswith("service_error=app_missing")
    assert "http://127.0.0.1:" in page(client, row, "account")     # the return address to register

    client.post(f"/utilities/automations/services/{row.slug}/app", data={"client_id": "app-1", "client_secret": "s"})
    db.session.refresh(row)
    assert secrets_store.load_secret(row.app_secret_ref) == {"client_id": "app-1", "client_secret": "s"}
    started = client.post(f"/utilities/automations/services/{row.slug}/test-account")
    query = parse_qs(urlsplit(started.headers["Location"]).query)
    assert started.headers["Location"].startswith("https://www.dropbox.com/oauth2/authorize")
    assert query["client_id"] == ["app-1"] and query["scope"] == ["files.content.read"]


def test_publishing_makes_the_service_available(client, app):
    row = new_service(client)
    services.save_draft(db.session, row.id, custom_immich())

    response = client.post(f"/utilities/automations/services/{row.slug}/publish")

    assert response.headers["Location"].endswith("notice=published")
    db.session.refresh(row)
    assert row.published and row.grants
    html = client.get("/utilities/automations").get_data(as_text=True)
    assert "A service you added." in html
    assert 'id="service-publish"' not in page(client, row)   # nothing new to publish


def test_publishing_without_a_passing_draft_is_refused(client, app):
    row = new_service(client)

    assert client.post(f"/utilities/automations/services/{row.slug}/publish").headers["Location"].endswith(
        "service_error=publish_refused")


def test_a_service_that_fails_the_current_check_offers_regenerate(client, app, queued):
    row = new_service(client)
    services.save_draft(db.session, row.id, custom_immich())
    services.publish(db.session, row.id)
    row.published = {**row.published, "contract": 0}
    db.session.commit()

    html = page(client, row)
    assert "Needs updating" in html and "Regenerate" in html

    client.post(f"/utilities/automations/services/{row.slug}/regenerate")
    assert queued and queued[0].startswith("Regenerate this service")


def test_a_service_in_use_cant_be_deleted(client, app, tmp_path):
    from yaffo.sync import setup

    row = new_service(client)
    services.save_draft(db.session, row.id, custom_immich())
    services.publish(db.session, row.id)
    setup.create_sync(db.session, "From my Immich", row.id)

    refused = client.post(f"/utilities/automations/services/{row.slug}/delete")
    assert refused.headers["Location"].endswith("service_error=service_in_use")

    other = new_service(client, "Unused")
    client.post(f"/utilities/automations/services/{other.slug}/delete")
    assert services.get_service(db.session, other.slug) is None


def test_answering_a_docs_request_continues_the_builder(client, app, queued):
    row = new_service(client)
    services.ask_for_docs(db.session, row.id, {"immich.app": ["https://immich.app/docs"],
                                                "example.org": ["https://example.org/x"]})
    assert "The builder asks to read pages from: immich.app, example.org" in page(client, row)
    message = client.get(f"/utilities/automations/services/{row.slug}/status").get_json()["messages"][-1]
    assert message["type"] == "permission" and isinstance(message["id"], int)

    answer = client.post(f"/utilities/automations/services/{row.slug}/permissions/{message['id']}",
                         json={"decision": "allow", "domains": ["immich.app"]})

    assert answer.get_json() == {"started": True}
    assert queued == ["I allowed reading pages from immich.app. I didn't allow example.org. Continue."]
    db.session.refresh(row)
    assert row.doc_domains == ["immich.app"]
    again = client.post(f"/utilities/automations/services/{row.slug}/permissions/{message['id']}",
                        json={"decision": "allow"})
    assert again.status_code == 409


def test_live_reads_can_be_allowed_and_revoked(client, app, queued, immich):
    row = new_service(client)
    services.save_draft(db.session, row.id, custom_immich())
    client.post(f"/utilities/automations/services/{row.slug}/test-account",
                data={"base_url": immich.base_url, "allow_http": "1", "api_key": "immich-test-key"})
    db.session.refresh(row)
    services.ask_for_live_reads(db.session, row.id, row.test_connection_key, "jason@example.com")
    message, _ = services.pending_permission(db.session, row.id, services.PERMISSION_LIVE_READS)

    client.post(f"/utilities/automations/services/{row.slug}/permissions/{message.id}", json={"decision": "allow"})

    db.session.refresh(row)
    assert services.live_reads_allowed(row) and "may make read-only calls" in page(client, row, "account")
    client.post(f"/utilities/automations/services/{row.slug}/live-reads/revoke")
    db.session.refresh(row)
    assert not services.live_reads_allowed(row)


FLICKR = Path(__file__).parents[3] / "yaffo" / "site_agents" / "adapter" / "examples" / "flickr_target.star"


@pytest.fixture
def flickr(monkeypatch):
    from scripts.fake_flickr import FakeFlickr

    with FakeFlickr() as fake:
        monkeypatch.setenv("YAFFO_SYNC_ORIGIN_OVERRIDES", fake.overrides)
        yield fake


def approve_and_return(client, authorize_url):
    """Approve at the fake Flickr and land on Yaffo's root with oauth_token / oauth_verifier."""
    import requests
    approve = requests.get(authorize_url.replace("/services/oauth/authorize?", "/_approve?"), allow_redirects=False,
                           timeout=10)
    back = client.get(f"/?{urlsplit(approve.headers['Location']).query}")
    assert back.status_code == 302
    return back.headers["Location"]


def test_an_oauth1_service_signs_in_its_test_account_with_the_users_app(client, app, flickr):
    from scripts.fake_flickr import CONSUMER_KEY, CONSUMER_SECRET

    row = new_service(client, "Flickr sets")
    assert services.save_draft(db.session, row.id, FLICKR.read_text()).ok
    html = page(client, row, "account")
    assert ">Callback URL</label>" in html and 'value="http://127.0.0.1:' in html
    assert ">Key</label>" in html and ">Secret</label>" in html
    refused = client.post(f"/utilities/automations/services/{row.slug}/test-account")
    assert refused.headers["Location"].endswith("service_error=app_missing")

    client.post(f"/utilities/automations/services/{row.slug}/app",
                data={"client_id": CONSUMER_KEY, "client_secret": CONSUMER_SECRET})
    started = client.post(f"/utilities/automations/services/{row.slug}/test-account")
    landed = approve_and_return(client, started.headers["Location"])

    assert landed.endswith(f"/utilities/automations/services/{row.slug}?tab=account")
    db.session.refresh(row)
    assert connections.get(db.session, row.test_connection_key).account_label == "jturan"
    report = client.post(f"/utilities/automations/services/{row.slug}/test").get_data(as_text=True)
    assert "Read · 200" in report     # flickr.photosets.getList, signed


def test_a_sync_using_an_oauth1_service_signs_in_through_the_browser(client, app, flickr):
    from scripts.fake_flickr import CONSUMER_KEY, CONSUMER_SECRET
    from yaffo.db.repositories import sync_repository
    from yaffo.sync import setup

    row = new_service(client, "Flickr sets")
    services.save_draft(db.session, row.id, FLICKR.read_text())
    services.publish(db.session, row.id)
    client.post(f"/utilities/automations/services/{row.slug}/app",
                data={"client_id": CONSUMER_KEY, "client_secret": CONSUMER_SECRET})
    automation = setup.create_sync(db.session, "To Flickr", row.id)
    html = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)
    assert "Sign in to Flickr sets" in html and 'name="api_key"' not in html and "another device" not in html

    started = client.post(f"/utilities/automations/{automation.slug}/sync/sign-in")
    approve_and_return(client, started.headers["Location"])

    key = sync_repository.load_sync(automation).config.connection_key
    assert connections.get(db.session, key).account_id == "12345678@N01"


def test_the_sidebar_lists_services_and_marks_drafts(client, app):
    draft = new_service(client, "Draft service")
    done = new_service(client, "My Immich")
    services.save_draft(db.session, done.id, custom_immich())
    services.publish(db.session, done.id)

    html = page(client, done)

    nav = html[html.index('id="automations-nav"'):html.index("new-automation-button")]
    assert "Services" in nav and "Draft service" in nav and "My Immich" in nav
    assert f'href="/utilities/automations/services/{done.slug}"\n                                   class="active"' in nav
    draft_item = nav[nav.index("Draft service"):nav.index("Draft service") + 120]
    assert "Draft</span>" in draft_item
    mine = nav[nav.index("My Immich"):nav.index("My Immich") + 120]
    assert "Draft</span>" not in mine


def test_the_services_section_appears_once_a_service_exists(client, app):
    def nav() -> str:
        html = client.get("/utilities/automations", follow_redirects=True).get_data(as_text=True)
        return html[html.index('id="automations-nav"'):html.index("new-automation-button")]

    assert ">Services</h3>" not in nav()
    new_service(client, "Flickr sets")
    assert ">Services</h3>" in nav()
