"""/internal/p2p/v1 against a fake engine: the three request checks, the error
shape, scope keys and source ids, destination validation (the write's security
boundary), and discovery through the web heartbeat."""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from yaffo.app import create_app
from yaffo.db import db
from yaffo.db.repositories import media_dir_repository, p2p_repository
from yaffo.p2p.errors import P2PServiceError
from yaffo.p2p.peer_api import PeerApiError, Scope
from yaffo.p2p.signaling import CallError
from yaffo.routes import internal_p2p as internal_module
from yaffo.sync.files import TEMP_PREFIX, TEMP_SUFFIX

pytestmark = pytest.mark.unit

PEER = "PEER-0000-0000-0001"
STRANGER = "STRA-0000-0000-0002"
INTERNAL = {"X-Yaffo-Internal": "1"}
BASE = "/internal/p2p/v1"


class FakePeerApi:
    def __init__(self):
        self.calls = []

    def open_session(self, peer, relay, budget, owner):
        self.calls.append(("open", peer, relay, budget, owner))
        return {"session_id": "s_1", "path": "lan", "idle_expires_in": 120}

    def session_info(self, session_id):
        if session_id != "s_1":
            raise PeerApiError("session_expired", "unknown or expired session")
        return {"session_id": "s_1", "path": "lan"}

    def close_session(self, session_id, owner):
        self.calls.append(("close", session_id, owner))

    def list_files(self, session_id, scope, cursor, limit, filters=None):
        self.calls.append(("files", session_id, scope, cursor, limit))
        self.filters = filters
        return {"files": [], "next_cursor": None, "total": 0}

    def start_pull(self, session_id, media_item_id, source, size, mtime, destination):
        self.calls.append(("pull", session_id, media_item_id, source, size, mtime, destination))
        return {"pull_id": "p_1"}

    def pull_status(self, pull_id, wait):
        self.calls.append(("status", pull_id, wait))
        return {"pull_id": pull_id, "state": "done", "sha256": "ab"}

    def cancel_pull(self, pull_id):
        self.calls.append(("cancel", pull_id))


class FakeService:
    def __init__(self):
        self.identity = SimpleNamespace(device_id="SELF-0000-0000-0000")
        self.hub_connected = True
        self.peer_api = FakePeerApi()
        self.online = {PEER}
        self.shared = {"status": "ok", "protocol": 2, "scopes": [
            {"scope_type": "album", "album_id": 12, "media_dir_id": None, "relative_path": None,
             "name": "For Mom", "file_count": 84},
            {"scope_type": "folder", "media_dir_id": "md1", "relative_path": "Vacation 2026",
             "name": "Pictures", "file_count": 3},
            {"scope_type": "media_dir", "media_dir_id": "md2", "relative_path": None,
             "name": "Phone", "file_count": 9},
        ]}
        self.files_error = None
        self.list_files_calls = []
        self.list_shared = SimpleNamespace(send=self._list_shared)
        self.list_files = SimpleNamespace(send=self._list_files)

    def connected_device_ids(self):
        return self.online

    def local_device_ids(self):
        return set()

    def _list_shared(self, device_id):
        if self.files_error is not None:
            raise self.files_error
        return self.shared

    def _list_files(self, device_id, media_dir_id, relative_path, filters, offset, limit, album_id=None):
        self.list_files_calls.append((media_dir_id, relative_path, album_id, offset, limit))
        self.filters = filters
        if self.files_error is not None:
            raise self.files_error
        return {"status": "ok", "total": 3, "offset": offset, "limit": limit, "files": [
            {"media_item_id": 9133, "media_dir_id": "md1", "relative_path": "Vacation 2026/a.jpg",
             "name": "a.jpg", "size": 10, "mtime": 1.0},
        ]}


@pytest.fixture
def media_root(tmp_path):
    root = tmp_path / "Pictures"
    (root / "Imports").mkdir(parents=True)
    return root


@pytest.fixture
def app(tmp_path, media_root):
    application = create_app(db_path=tmp_path / "internal.db", config={"TESTING": True})
    with application.app_context():
        db.create_all()
        media_dir_repository.add_media_dir(db.session, str(media_root))
        p2p_repository.upsert_trusted_device(db.session, PEER, "pubkey", "Mom's laptop")
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def service(app):
    fake = FakeService()
    app.extensions["p2p_service"] = fake
    return fake


@pytest.fixture
def client(app):
    return app.test_client()


def get(client, path, **kwargs):
    return client.get(BASE + path, headers={**INTERNAL, **kwargs.pop("headers", {})}, **kwargs)


def post(client, path, json, **kwargs):
    return client.post(BASE + path, json=json, headers={**INTERNAL, **kwargs.pop("headers", {})}, **kwargs)


# ---- the request checks ------------------------------------------------------------


def test_a_local_process_gets_through(client, service):
    response = get(client, "/status")
    assert response.status_code == 200
    assert response.get_json() == {
        "available": True, "device_id": "SELF-0000-0000-0000", "hub_connected": True, "version": 1,
    }


def test_the_custom_header_is_required(client, service):
    response = client.get(BASE + "/status")
    assert response.status_code == 403
    assert response.get_json() == {"error": {"code": "internal_only",
                                             "message": "this API serves local Yaffo processes only"}}


def test_another_machine_is_refused(client, service):
    response = get(client, "/status", environ_overrides={"REMOTE_ADDR": "192.168.1.20"})
    assert response.status_code == 403


def test_a_non_loopback_host_is_refused_even_when_the_ui_allows_it(tmp_path, service):
    """web.allowed_hosts opens the UI to a LAN name; the internal API stays loopback-only."""
    application = create_app(db_path=tmp_path / "lan.db",
                             config={"TESTING": True, "WEB_ALLOWED_HOSTS": ["yaffo.lan"]})
    application.extensions["p2p_service"] = service
    response = application.test_client().get(BASE + "/status", headers={**INTERNAL, "Host": "yaffo.lan:5001"})
    assert response.status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:5001", "localhost", "[::1]:5001"])
def test_loopback_host_names_pass(client, service, host):
    assert get(client, "/status", headers={"Host": host}).status_code == 200


def test_production_posts_need_no_csrf_token(tmp_path, media_root):
    """Workers have no browser session; the header check stands in for CSRF."""
    application = create_app(db_path=tmp_path / "prod.db",
                             config={"TESTING": False, "CSRF_ENABLED": True, "SECRET_KEY": "x"})
    with application.app_context():
        db.create_all()
        p2p_repository.upsert_trusted_device(db.session, PEER, "pubkey", "Mom's laptop")
    application.extensions["p2p_service"] = FakeService()
    response = application.test_client().post(
        f"{BASE}/devices/{PEER}/sessions", json={"owner": "sync-1"}, headers=INTERNAL,
    )
    assert response.status_code == 201


# ---- status, devices, scopes, files ------------------------------------------------


def test_status_without_the_engine(client):
    assert get(client, "/status").get_json()["available"] is False


def test_devices_without_the_engine_is_unavailable(client):
    response = get(client, "/devices")
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "engine_unavailable"


def test_devices_report_presence(client, service, app):
    with app.app_context():
        p2p_repository.upsert_trusted_device(db.session, STRANGER, "pk2", "Old phone")
        p2p_repository.mark_device_revoked(db.session, STRANGER)
        p2p_repository.touch_last_seen(db.session, PEER)

    rows = {row["device_id"]: row for row in get(client, "/devices").get_json()["devices"]}

    assert rows[PEER]["online"] is True and rows[PEER]["trust_state"] == "trusted"
    assert rows[PEER]["display_name"] == "Mom's laptop"
    assert datetime.fromisoformat(rows[PEER]["last_seen_at"]).year >= 2026
    assert rows[STRANGER]["online"] is False and rows[STRANGER]["trust_state"] == "revoked"


def test_unknown_presence_is_null_not_offline(client, service):
    service.online = None
    assert get(client, "/devices").get_json()["devices"][0]["online"] is None


def test_scopes_carry_their_keys(client, service):
    body = get(client, f"/devices/{PEER}/scopes").get_json()
    assert body["protocol"] == 2
    assert [scope["scope_key"] for scope in body["scopes"]] == [
        "album:12", "folder:md1:Vacation 2026", "media_dir:md2",
    ]


def test_scopes_of_an_untrusted_device_are_refused(client, service):
    response = get(client, f"/devices/{STRANGER}/scopes")
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "not_trusted"


@pytest.mark.parametrize(("error", "code", "status"), [
    (CallError("not connected"), "peer_offline", 409),
    (P2PServiceError("SELF-0000-0000-0000 is not a trusted device — pair first"), "not_trusted", 403),
])
def test_scopes_map_peer_failures(client, service, error, code, status):
    service.files_error = error
    response = get(client, f"/devices/{PEER}/scopes")
    assert response.status_code == status
    assert response.get_json()["error"]["code"] == code


def test_files_add_the_stable_source_id(client, service):
    body = get(client, f"/devices/{PEER}/files", query_string={"scope": "folder:md1:Vacation 2026"}).get_json()

    assert service.list_files_calls == [("md1", "Vacation 2026", None, 0, 200)]
    assert body["files"][0]["source_id"] == "md1:Vacation 2026/a.jpg"
    assert body["next_cursor"] is None
    assert body["total"] == 3


def test_files_page_from_a_cursor_and_parse_album_keys(client, service):
    get(client, f"/devices/{PEER}/files", query_string={"scope": "album:12", "cursor": "200", "limit": "50"})
    assert service.list_files_calls == [("", "", 12, 200, 50)]


def test_files_over_a_session_go_through_it(client, service):
    get(client, f"/devices/{PEER}/files", query_string={"scope": "media_dir:md2", "session": "s_1"})
    assert service.peer_api.calls == [("files", "s_1", Scope(media_dir_id="md2"), 0, 200)]
    assert service.list_files_calls == []


def test_files_pass_a_syncs_filters_to_the_peer(client, service):
    filters = {"year": 2024, "people": [3]}
    get(client, f"/devices/{PEER}/files", query_string={"scope": "album:12", "filters": json.dumps(filters)})
    assert service.filters == filters

    get(client, f"/devices/{PEER}/files", query_string={"scope": "album:12", "session": "s_1",
                                                        "filters": json.dumps(filters)})
    assert service.peer_api.filters == filters


def test_files_without_filters_list_the_whole_scope(client, service):
    get(client, f"/devices/{PEER}/files", query_string={"scope": "album:12"})
    assert service.filters == {}


@pytest.mark.parametrize("filters", ["not json", "[1]", '{"nonsense": 1}'])
def test_files_reject_bad_filters(client, service, filters):
    response = get(client, f"/devices/{PEER}/files", query_string={"scope": "album:12", "filters": filters})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_request"
    assert service.list_files_calls == []


@pytest.mark.parametrize("scope", ["", "album:x", "folder:md1", "nonsense:1"])
def test_files_reject_bad_scope_keys(client, service, scope):
    response = get(client, f"/devices/{PEER}/files", query_string={"scope": scope})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_request"


def test_a_scope_the_peer_no_longer_shares(client, service):
    service.files_error = P2PServiceError("no active share grant covers this scope")
    response = get(client, f"/devices/{PEER}/files", query_string={"scope": "media_dir:md2"})
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "scope_not_shared"


# ---- sessions --------------------------------------------------------------------


def test_open_session_defaults_to_never_relaying(client, service):
    response = post(client, f"/devices/{PEER}/sessions", {"owner": "sync-27"})
    assert response.status_code == 201
    assert service.peer_api.calls == [("open", PEER, "never", 0, "sync-27")]


def test_open_session_with_a_relay_budget(client, service):
    post(client, f"/devices/{PEER}/sessions", {"owner": "sync-27", "relay": "budget", "relay_budget_bytes": 1000})
    assert service.peer_api.calls == [("open", PEER, "budget", 1000, "sync-27")]


@pytest.mark.parametrize("body", [{}, {"owner": "x", "relay": "always"}, {"owner": "x", "relay": "budget",
                                                                         "relay_budget_bytes": -1}])
def test_open_session_validates_its_body(client, service, body):
    response = post(client, f"/devices/{PEER}/sessions", body)
    assert response.status_code == 400


def test_unknown_sessions_have_expired(client, service):
    response = get(client, "/sessions/s_gone")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "session_expired"


def test_closing_a_session_releases_one_owner(client, service):
    response = client.delete(f"{BASE}/sessions/s_1?owner=sync-27", headers=INTERNAL)
    assert response.status_code == 204
    assert service.peer_api.calls == [("close", "s_1", "sync-27")]


# ---- pulls and their destination ---------------------------------------------------


def temp_name(tag="27-3b9e"):
    return f"{TEMP_PREFIX}{tag}{TEMP_SUFFIX}"


def pull_body(destination, **overrides):
    return {"media_item_id": 9133, "source_id": "md1:a.jpg",
            "expected": {"size": 10, "mtime": 1.5}, "destination": str(destination), **overrides}


def test_a_pull_into_a_media_dir_starts(client, service, media_root):
    destination = media_root / "Imports" / temp_name()

    response = post(client, "/sessions/s_1/pulls", pull_body(destination))

    assert response.status_code == 202
    assert response.get_json() == {"pull_id": "p_1"}
    assert service.peer_api.calls == [
        ("pull", "s_1", 9133, "md1:a.jpg", 10, 1.5, destination.resolve()),
    ]


@pytest.mark.parametrize("make_destination", [
    lambda root, tmp: "Imports/" + temp_name(),                       # relative
    lambda root, tmp: root / "Imports" / "photo.jpg",                 # not a temp name
    lambda root, tmp: root / "Imports" / f"{TEMP_PREFIX}{TEMP_SUFFIX}",  # an empty temp name
    lambda root, tmp: root / "Missing" / temp_name(),                 # folder doesn't exist
    lambda root, tmp: tmp / temp_name(),                              # outside every media dir
    lambda root, tmp: root / "Imports" / ".." / ".." / temp_name(),   # escapes by ..
    lambda root, tmp: None,
])
def test_bad_destinations_are_refused(client, service, media_root, tmp_path, make_destination):
    destination = make_destination(media_root, tmp_path)

    response = post(client, "/sessions/s_1/pulls", pull_body(destination))

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "bad_destination"
    assert service.peer_api.calls == []


def test_a_symlinked_destination_is_refused(client, service, media_root, tmp_path):
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("keep")
    destination = media_root / "Imports" / temp_name()
    destination.symlink_to(outside)

    response = post(client, "/sessions/s_1/pulls", pull_body(destination))

    assert response.get_json()["error"]["code"] == "bad_destination"


def test_a_folder_that_links_out_of_the_media_dir_is_refused(client, service, media_root, tmp_path):
    (tmp_path / "outside").mkdir()
    (media_root / "Imports" / "link").symlink_to(tmp_path / "outside", target_is_directory=True)

    response = post(client, "/sessions/s_1/pulls", pull_body(media_root / "Imports" / "link" / temp_name()))

    assert response.get_json()["error"]["code"] == "bad_destination"


def test_pull_status_long_polls_at_most_ten_seconds(client, service):
    response = get(client, "/pulls/p_1", query_string={"wait": "10"})
    assert response.get_json()["state"] == "done"
    assert service.peer_api.calls == [("status", "p_1", 10.0)]


def test_cancel_a_pull(client, service):
    assert client.delete(f"{BASE}/pulls/p_1", headers=INTERNAL).status_code == 204
    assert service.peer_api.calls == [("cancel", "p_1")]


# ---- discovery ---------------------------------------------------------------------


def test_the_heartbeat_publishes_the_served_port(client, service, monkeypatch):
    monkeypatch.setattr(internal_module, "_served_port", {})
    with pytest.raises(LookupError):
        internal_module.internal_api_status()

    get(client, "/status", environ_overrides={"SERVER_PORT": "5123"})

    assert internal_module.internal_api_status() == {"port": 5123, "version": 1}


def test_internal_routes_are_kept_out_of_the_assistants_pages():
    from yaffo.site_agents.assistant.app_pages import NOT_PAGES
    assert {"internal_p2p.status", "internal_p2p.devices", "internal_p2p.pull_status"} <= NOT_PAGES
