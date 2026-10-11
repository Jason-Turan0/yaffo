"""Route tests for a key-based, self-hosted service (Immich): the New automation
dialog, the Account step's key form with the server address and the http warning,
the review naming the server, and a Test through the signed-in account."""
import json
from pathlib import Path

import pytest

from yaffo.db import db
from yaffo.db.models import ApplicationSettings, Automation, SyncAdapter
from yaffo.db.repositories import sync_repository
from yaffo.sync import connections

pytestmark = pytest.mark.unit

MEDIA_DIR_ID = "6f1c0000-0000-4000-8000-0000000000cc"


@pytest.fixture(autouse=True)
def secrets_file(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "secrets.json"
    monkeypatch.setenv("YAFFO_SYNC_SECRETS_FILE", str(path))
    return path


@pytest.fixture
def immich():
    from scripts.fake_immich import FakeImmich

    with FakeImmich(photos=4, album="Family") as server:
        yield server


@pytest.fixture
def library(app, tmp_path) -> Path:
    root = tmp_path / "Pictures"
    root.mkdir()
    db.session.add(ApplicationSettings(name="media_dirs", type="json",
                                       value=json.dumps([{"id": MEDIA_DIR_ID, "path": str(root)}])))
    db.session.commit()
    return root


def create(client) -> Automation:
    html = client.get("/utilities/automations").get_data(as_text=True)
    assert "Connect with an API key from your server" in html
    adapter_id = db.session.query(SyncAdapter.id).filter_by(slug="preset.immich_source").scalar()
    response = client.post("/utilities/automations/create",
                           data={"name": "From Immich", "kind": "import", "adapter_id": adapter_id})
    assert response.status_code == 302
    return db.session.query(Automation).filter_by(name="From Immich").one()


def connect(client, automation, base_url, key="immich-test-key", allow_http=True):
    data = {"base_url": base_url, "api_key": key}
    if allow_http:
        data["allow_http"] = "1"
    return client.post(f"/utilities/automations/{automation.slug}/sync/credentials", data=data)


def test_the_account_step_asks_for_the_server_and_a_key(client, library, immich):
    automation = create(client)

    html = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)

    assert 'name="base_url"' in html and 'name="api_key"' in html and 'name="allow_http"' in html
    assert "Account Settings › API Keys" in html and "Connect to Immich" in html
    assert "Sign in to Immich in your browser" not in html and "Signing in on another device?" not in html


def test_connecting_with_a_key_signs_in_and_names_the_server(client, library, immich):
    automation = create(client)

    response = connect(client, automation, immich.base_url)

    assert response.status_code == 302 and "sync_error" not in response.headers["Location"]
    config = sync_repository.load_sync(automation).config
    connection = connections.get(db.session, config.connection_key)
    assert connection.instance == {"base_url": immich.base_url}
    host = immich.base_url.split("://", 1)[1]
    client.post(f"/utilities/automations/{automation.slug}/sync/options",
                data={"album": json.dumps({"id": "album-1", "label": "Family"})})
    client.post(f"/utilities/automations/{automation.slug}/sync/import-root",
                data={"media_dir_id": MEDIA_DIR_ID, "subpath": "Imports/Immich", "layout": "year_month"})
    html = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)
    assert f"jason@example.com · {host}" in html
    assert 'id="syncEnableModal"' in html and f"<dd>{host}</dd>" in html  # "Yaffo talks to"


def test_http_needs_the_warning_confirmed(client, library, immich):
    automation = create(client)

    response = connect(client, automation, immich.base_url, allow_http=False)

    assert response.headers["Location"].endswith("sync_error=instance_http_unconfirmed")
    html = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Confirm the warning to connect to it anyway" in html
    assert sync_repository.load_sync(automation).config.connection_key is None


def test_a_refused_key_is_reported(client, library, immich):
    automation = create(client)

    response = connect(client, automation, immich.base_url, key="wrong")

    assert response.headers["Location"].endswith("sync_error=rejected")
    assert connections.find_for_provider(db.session, "immich") == []


def test_a_public_http_address_is_refused(client, library, immich):
    automation = create(client)

    response = connect(client, automation, "http://photos.example.com")

    assert response.headers["Location"].endswith("sync_error=instance_http_public")


def test_the_album_picker_and_test_read_through_the_key(client, library, immich):
    automation = create(client)
    connect(client, automation, immich.base_url)

    items = client.get(f"/utilities/automations/{automation.slug}/sync/choices?option=album").get_json()["items"]
    assert [i["label"] for i in items] == ["Everything in the library", "Family"]
    client.post(f"/utilities/automations/{automation.slug}/sync/options",
                data={"album": json.dumps({"id": "album-1", "label": "Family"})})
    test = client.post(f"/utilities/automations/{automation.slug}/sync/test").get_json()
    assert test["problem"] is None and test["state_counts"] == {"new": 4}
    assert immich.state.counters.get("downloads", 0) == 0


def test_browser_sign_in_is_not_offered_for_a_key_service(client, library, immich):
    automation = create(client)

    assert client.post(f"/utilities/automations/{automation.slug}/sync/sign-in").status_code == 400
