"""Route tests for web syncs (the OneDrive source): the New automation dialog, the
Account step with browser and device-code sign-in, the folder picker, the review
before enabling, Test, and deleting a sync releasing its account."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from yaffo.db import db
from yaffo.db.models import ApplicationSettings, Automation, SyncAdapter
from yaffo.db.repositories import sync_repository
from yaffo.sync import connections, oauth, pending_sign_ins

pytestmark = pytest.mark.unit

MEDIA_DIR_ID = "6f1c0000-0000-4000-8000-0000000000bb"


@pytest.fixture(autouse=True)
def secrets_file(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "secrets.json"
    monkeypatch.setenv("YAFFO_SYNC_SECRETS_FILE", str(path))
    return path


@pytest.fixture
def fake(monkeypatch):
    from scripts.fake_onedrive import FakeOneDrive

    with FakeOneDrive(photos=6, page_size=50) as server:
        monkeypatch.setenv("YAFFO_SYNC_ORIGIN_OVERRIDES", server.overrides)
        yield server


@pytest.fixture
def library(app, tmp_path) -> Path:
    root = tmp_path / "Pictures"
    root.mkdir()
    db.session.add_all([
        SyncAdapter(slug="builtin.folder_source", name="Folder", role="source", provider="local", is_preset=True,
                    handler="folder_source", grants=[], doc_domains=[]),
        ApplicationSettings(name="media_dirs", type="json",
                            value=json.dumps([{"id": MEDIA_DIR_ID, "path": str(root)}])),
    ])
    db.session.commit()
    return root


def onedrive_id() -> int:
    return db.session.query(SyncAdapter.id).filter_by(slug="preset.onedrive_source").scalar()


def create(client) -> Automation:
    page = client.get("/utilities/automations")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "OneDrive" in html and "Sign in with your Microsoft account" in html
    resp = client.post("/utilities/automations/create",
                       data={"name": "My OneDrive", "kind": "import", "adapter_id": onedrive_id()})
    assert resp.status_code == 302
    return db.session.query(Automation).filter_by(name="My OneDrive").one()


def sign_in(client, automation, fresh=False) -> None:
    """Click Sign in, approve at the fake provider, and land on Yaffo's root."""
    resp = client.post(f"/utilities/automations/{automation.slug}/sync/sign-in",
                       data={"fresh": "1"} if fresh else {})
    assert resp.status_code == 302
    provider_url = resp.headers["Location"]
    query = parse_qs(urlsplit(provider_url).query)
    assert query["redirect_uri"] == ["http://localhost:80"] or query["redirect_uri"][0].startswith("http://localhost")
    landing = requests.get(provider_url.replace("/authorize?", "/approve?"), allow_redirects=False, timeout=10)
    returned = urlsplit(landing.headers["Location"])
    assert returned.path in ("", "/")
    back = client.get(f"/?{returned.query}")
    assert back.status_code == 302
    assert back.headers["Location"].endswith(f"/utilities/automations/{automation.slug}")


def test_a_new_onedrive_sync_starts_at_the_account_step(client, library, fake):
    automation = create(client)
    html = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)
    assert "Sign in to OneDrive" in html
    assert "Sign in first: the folder is chosen from your account." in html
    assert "Signing in on another device?" in html


def test_sign_in_then_pick_a_folder_then_review_and_enable(client, library, fake):
    automation = create(client)
    sign_in(client, automation)
    db.session.refresh(automation)
    config = sync_repository.load_sync(automation).config
    assert config.connection_key == "sync_connection:onedrive:fake-user-1"
    html = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)
    assert 'class="sync-account-name">jason@example.com<' in html and "Signed in" in html
    assert 'data-remote-option="folder"' in html

    top = client.get(f"/utilities/automations/{automation.slug}/sync/choices?option=folder").get_json()
    assert top["items"][0] == {"id": "CAMROLL", "label": "Camera Roll", "has_children": True, "suggested": True}
    assert {"Camera Roll", "Documents"} <= {item["label"] for item in top["items"]}
    inside = client.get(f"/utilities/automations/{automation.slug}/sync/choices?option=folder&parent=CAMROLL")
    assert [item["label"] for item in inside.get_json()["items"]] == ["2024"]

    resp = client.post(f"/utilities/automations/{automation.slug}/sync/options",
                       data={"folder": json.dumps({"id": "CAMROLL", "label": "Camera Roll"})})
    assert resp.status_code == 302 and "sync_error" not in resp.headers["Location"]
    html = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)
    assert "Imports new photos from OneDrive › Camera Roll" in html
    # Enabling a web sync goes through a review of what it will do.
    assert 'id="sync-enable-review"' in html and 'id="syncEnableModal"' in html
    assert "graph.microsoft.com" in html and "Kept in your library" in html

    test = client.post(f"/utilities/automations/{automation.slug}/sync/test").get_json()
    assert test["problem"] is None and test["would_add"] == 6
    assert fake.state.counters.get("downloads", 0) == 0  # Test downloads nothing

    resp = client.post(f"/utilities/automations/{automation.slug}/sync/turn-on")
    assert resp.status_code == 302
    db.session.refresh(automation)
    assert automation.enabled


def test_the_picker_needs_a_signed_in_account(client, library, fake):
    automation = create(client)
    resp = client.get(f"/utilities/automations/{automation.slug}/sync/choices?option=folder")
    assert resp.status_code == 409
    assert client.get(f"/utilities/automations/{automation.slug}/sync/choices?option=nope").status_code == 404


def test_a_refused_sign_in_comes_back_with_an_error(client, library, fake):
    automation = create(client)
    resp = client.post(f"/utilities/automations/{automation.slug}/sync/sign-in")
    state = parse_qs(urlsplit(resp.headers["Location"]).query)["state"][0]
    back = client.get(f"/?state={state}&error=access_denied")
    assert back.status_code == 302 and "sync_error=sign_in_failed" in back.headers["Location"]
    html = client.get(back.headers["Location"].split("localhost", 1)[-1]).get_data(as_text=True)
    assert "The sign-in didn&#39;t complete" in html or "The sign-in didn't complete" in html


def test_the_root_ignores_states_it_did_not_start(client, library):
    resp = client.get("/?state=unknown&code=x")
    assert resp.status_code != 302 or "sync_error" not in resp.headers.get("Location", "")


def test_device_code_sign_in(client, library, fake):
    automation = create(client)
    start = client.post(f"/utilities/automations/{automation.slug}/sync/device-code").get_json()
    assert start["user_code"] and start["verification_uri"].endswith("/devicelogin")
    poll = client.post(f"/utilities/automations/{automation.slug}/sync/device-code/poll",
                       data={"handle": start["handle"]}).get_json()
    assert poll == {"status": "pending"}
    requests.post(start["verification_uri"], data={"code": start["user_code"]}, timeout=10)
    pending = pending_sign_ins.load(db.session, start["handle"])
    pending.next_poll = 0
    pending_sign_ins.update(db.session, pending)
    poll = client.post(f"/utilities/automations/{automation.slug}/sync/device-code/poll",
                       data={"handle": start["handle"]}).get_json()
    assert poll == {"status": "done"}
    db.session.refresh(automation)
    assert sync_repository.load_sync(automation).config.connection_key == "sync_connection:onedrive:fake-user-1"


def test_a_second_sync_reuses_the_account_and_deleting_releases_it_last(client, library, fake, secrets_file):
    first = create(client)
    sign_in(client, first)
    resp = client.post("/utilities/automations/create",
                       data={"name": "Second", "kind": "import", "adapter_id": onedrive_id()})
    second = db.session.query(Automation).filter_by(name="Second").one()
    assert sync_repository.load_sync(second).config.connection_key == "sync_connection:onedrive:fake-user-1"
    html = client.get(f"/utilities/automations/{second.slug}").get_data(as_text=True)
    assert "Also used by 1 other sync" in html
    client.post(f"/utilities/automations/{first.slug}/delete")
    assert connections.get(db.session, "sync_connection:onedrive:fake-user-1") is not None
    client.post(f"/utilities/automations/{second.slug}/delete")
    assert connections.get(db.session, "sync_connection:onedrive:fake-user-1") is None
    assert "fake-user-1" not in secrets_file.read_text()


def test_a_revoked_account_asks_to_sign_in_again(client, library, fake):
    automation = create(client)
    sign_in(client, automation)
    connections.mark_needs_sign_in(db.engine, "sync_connection:onedrive:fake-user-1")
    db.session.expire_all()
    html = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)
    assert "needs you to sign in to jason@example.com again" in html
    test = client.post(f"/utilities/automations/{automation.slug}/sync/test").get_json()
    assert test["problem"] == "needs_sign_in"
    sign_in(client, automation)
    db.session.expire_all()
    html = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)
    assert "needs you to sign in" not in html
