"""Connections (shared accounts) and OAuth sign-in, against the fake Microsoft
identity platform."""
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from sqlalchemy.orm import Session

from yaffo.db.models import ApplicationSettings, SyncAdapter
from yaffo.sync import connections, oauth, pending_sign_ins, secrets_store, setup
from yaffo.sync.preset_seed import ensure_presets
from yaffo.sync.providers import ONEDRIVE
from yaffo.sync.schemas import CONNECTION_NEEDS_SIGN_IN, CONNECTION_READY


def skip_poll_wait(session, handle: str) -> None:
    """Don't wait out the provider's polling interval in a test."""
    pending = pending_sign_ins.load(session, handle)
    pending.next_poll = 0
    pending_sign_ins.update(session, pending)


def browser_approves(sign_in_url: str) -> dict[str, str]:
    """What the browser does: open the provider page, click Accept, and land on the
    return address. Returns the return address's query."""
    page = requests.get(sign_in_url, timeout=10)
    assert page.status_code == 200 and "Accept" in page.text
    params = parse_qs(urlsplit(sign_in_url).query)
    approve = sign_in_url.replace("/authorize?", "/approve?")
    landing = requests.get(approve, allow_redirects=False, timeout=10)
    assert landing.status_code == 302
    location = landing.headers["location"]
    assert location.startswith(params["redirect_uri"][0])
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


def test_browser_sign_in_with_pkce_stores_a_shared_connection(session, fake_onedrive, secrets_file):
    scopes = oauth.requested_scopes(ONEDRIVE, ["Files.Read"])
    assert scopes == ["offline_access", "User.Read", "Files.Read"]
    url = oauth.begin_sign_in(session, ONEDRIVE, scopes, 5001, "http://127.0.0.1:5001/back", "my-sync")
    query = parse_qs(urlsplit(url).query)
    assert query["redirect_uri"] == ["http://localhost:5001"]  # the bare loopback root
    assert query["code_challenge_method"] == ["S256"] and query["client_id"] == [ONEDRIVE.client_id()]
    returned = browser_approves(url)
    assert oauth.pending_sign_in(session, returned["state"]) is not None
    signed_in = oauth.finish_sign_in(session, returned["state"], returned)
    assert signed_in.automation_slug == "my-sync" and signed_in.return_to == "http://127.0.0.1:5001/back"
    assert signed_in.connection_key == "sync_connection:onedrive:fake-user-1"
    connection = connections.get(session, signed_in.connection_key)
    assert connection.account_label == "jason@example.com" and connection.status == CONNECTION_READY
    assert "Files.Read" in connection.scopes
    assert secrets_store.load_secret(connection.secret_ref)["refresh_token"].startswith("RT-")
    assert oauth.pending_sign_in(session, returned["state"]) is None
    # Signing in to the same account again updates the one row.
    again = oauth.finish_sign_in(session, *(lambda q: (q["state"], q))(
        browser_approves(oauth.begin_sign_in(session, ONEDRIVE, scopes, 5001, "/", None))))
    assert again.connection_key == signed_in.connection_key
    assert session.query(ApplicationSettings).filter(ApplicationSettings.name.like("sync_connection:%")).count() == 1


def test_sign_in_rejects_unknown_state_and_provider_errors(session, fake_onedrive):
    with pytest.raises(oauth.SignInError) as unknown:
        oauth.finish_sign_in(session, "nope", {"code": "x"})
    assert unknown.value.code == "unknown_state"
    url = oauth.begin_sign_in(session, ONEDRIVE, ["Files.Read"], 5001, "/", None)
    state = parse_qs(urlsplit(url).query)["state"][0]
    with pytest.raises(oauth.SignInError) as denied:
        oauth.finish_sign_in(session, state, {"state": state, "error": "access_denied"})
    assert denied.value.code == "access_denied"


def test_device_code_sign_in(session, fake_onedrive):
    handle, pending = oauth.begin_device_code(session, ONEDRIVE, ["Files.Read"], "my-sync")
    assert pending.user_code and pending.verification_uri.endswith("/devicelogin")
    assert oauth.poll_device_code(session, handle) == (oauth.DEVICE_PENDING, None)
    requests.post(pending.verification_uri, data={"code": pending.user_code}, timeout=10)
    skip_poll_wait(session, handle)
    status, signed_in = oauth.poll_device_code(session, handle)
    assert status == oauth.DEVICE_DONE and signed_in.connection_key == "sync_connection:onedrive:fake-user-1"
    with pytest.raises(oauth.SignInError):
        oauth.poll_device_code(session, handle)


def test_refresh_lease_is_exclusive(session):
    key = connections.upsert_after_sign_in(session, "onedrive", "acct", None, [], {"access_token": "a"})
    bind = session.get_bind()
    assert connections.take_refresh_lease(bind, key)
    assert not connections.take_refresh_lease(bind, key)
    connections.release_refresh_lease(bind, key)
    assert connections.take_refresh_lease(bind, key)


def test_mark_needs_sign_in_uses_its_own_transaction(session):
    key = connections.upsert_after_sign_in(session, "onedrive", "acct", None, [], {"access_token": "a"})
    connections.mark_needs_sign_in(session.get_bind(), key)
    session.expire_all()
    assert connections.get(session, key).status == CONNECTION_NEEDS_SIGN_IN


def test_unusual_account_ids_are_hashed_into_the_key():
    assert connections.connection_key("onedrive", "a b/c").startswith("sync_connection:onedrive:h")
    assert connections.connection_key("onedrive", "abc-1") == "sync_connection:onedrive:abc-1"


def test_connections_are_shared_preselected_and_released(session, library, fake_onedrive):
    ensure_presets(session)
    adapter = session.query(SyncAdapter).filter_by(slug="preset.onedrive_source").one()
    first = connections.upsert_after_sign_in(session, "onedrive", "one", "one@example.com", ["Files.Read"],
                                             fake_onedrive.grant_token())
    a = setup.create_sync(session, "A", adapter.id)
    b = setup.create_sync(session, "B", adapter.id)
    assert connections.references(session, first) == [a.id, b.id]
    second = connections.upsert_after_sign_in(session, "onedrive", "two", "two@example.com", ["Files.Read"],
                                              fake_onedrive.grant_token())
    ref = connections.get(session, first).secret_ref
    setup.set_connection(session, a, second)
    assert connections.get(session, first) is not None  # B still uses it
    setup.set_connection(session, b, second)
    assert connections.get(session, first) is None
    assert secrets_store.load_secret(ref) is None


def test_switching_accounts_clears_remote_options_and_consent(session, library, onedrive_sync, fake_onedrive):
    automation = onedrive_sync()
    assert automation.enabled
    other = connections.upsert_after_sign_in(session, "onedrive", "two", "two@example.com", ["Files.Read"],
                                             fake_onedrive.grant_token())
    setup.set_connection(session, automation, other)
    assert not automation.enabled
    assert setup.missing_setup(session, automation) == ["option:folder"]


def test_an_account_without_the_needed_scope_blocks_turning_on(session, library, fake_onedrive):
    ensure_presets(session)
    adapter = session.query(SyncAdapter).filter_by(slug="preset.onedrive_source").one()
    connections.upsert_after_sign_in(session, "onedrive", "x", None, ["User.Read"], fake_onedrive.grant_token())
    automation = setup.create_sync(session, "A", adapter.id)
    assert "account" in setup.missing_setup(session, automation)


def test_a_transient_whoami_failure_after_sign_in_is_retried(session, fake_onedrive, monkeypatch):
    monkeypatch.setattr(oauth, "WHOAMI_RETRY_SECONDS", 0)
    fake_onedrive.state.me_failures = 2
    url = oauth.begin_sign_in(session, ONEDRIVE, ["Files.Read"], 5001, "/", None)
    returned = browser_approves(url)
    signed_in = oauth.finish_sign_in(session, returned["state"], returned)
    assert signed_in.connection_key == "sync_connection:onedrive:fake-user-1"

    fake_onedrive.state.me_failures = 5
    returned = browser_approves(oauth.begin_sign_in(session, ONEDRIVE, ["Files.Read"], 5001, "/", None))
    with pytest.raises(oauth.SignInError) as failed:
        oauth.finish_sign_in(session, returned["state"], returned)
    assert failed.value.code == "whoami_failed"


def test_a_pending_sign_in_survives_a_restart_and_keeps_its_secret_out_of_the_db(session, fake_onedrive,
                                                                                 secrets_file):
    url = oauth.begin_sign_in(session, ONEDRIVE, ["Files.Read"], 5001, "/back", "my-sync")
    returned = browser_approves(url)
    row = session.query(ApplicationSettings).filter(
        ApplicationSettings.name == f"sync_pending_sign_in:{returned['state']}").one()
    assert "verifier" not in row.value and "code_verifier" not in row.value
    assert f"sync.pending.{returned['state']}" in secrets_file.read_text()

    # A restart: nothing in this process remembers the sign-in; a new session finishes it.
    restarted = Session(bind=session.get_bind())
    signed_in = oauth.finish_sign_in(restarted, returned["state"], returned)
    assert signed_in.automation_slug == "my-sync"
    assert f"sync.pending.{returned['state']}" not in secrets_file.read_text()
    # Single use: the same return address can't redeem it again.
    with pytest.raises(oauth.SignInError) as again:
        oauth.finish_sign_in(restarted, returned["state"], returned)
    assert again.value.code == "unknown_state"
    restarted.close()


def test_a_device_code_survives_a_restart(session, fake_onedrive):
    handle, pending = oauth.begin_device_code(session, ONEDRIVE, ["Files.Read"], "my-sync")
    requests.post(pending.verification_uri, data={"code": pending.user_code}, timeout=10)
    restarted = Session(bind=session.get_bind())
    skip_poll_wait(restarted, handle)
    status, signed_in = oauth.poll_device_code(restarted, handle)
    assert status == oauth.DEVICE_DONE and signed_in.automation_slug == "my-sync"
    restarted.close()


def test_expired_sign_ins_are_purged_with_their_secrets(session, fake_onedrive, secrets_file, monkeypatch):
    url = oauth.begin_sign_in(session, ONEDRIVE, ["Files.Read"], 5001, "/", None)
    state = parse_qs(urlsplit(url).query)["state"][0]
    later = __import__("time").time() + oauth.PENDING_TTL_SECONDS + 1
    monkeypatch.setattr(pending_sign_ins.time, "time", lambda: later)
    assert oauth.pending_sign_in(session, state) is None
    assert session.query(ApplicationSettings).filter(
        ApplicationSettings.name.like("sync_pending_sign_in:%")).count() == 0
    assert f"sync.pending.{state}" not in secrets_file.read_text()
