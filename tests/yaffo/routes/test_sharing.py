"""Route tests for the Sharing tab: sidebar + settings page rendering
(identity, presence tri-state, revoked rows), pairing-code generation, the
accept flow's success/error paths, per-device pages (rename, revoke), and
revocation — all against a stub P2PService so no sockets or keychain are
involved. The real engine is exercised end-to-end through these same routes
in tests/yaffo/p2p/test_service_integration.py."""
import json
from types import SimpleNamespace

import pytest

from yaffo.db import db
from yaffo.db.models import (
    GRANT_SCOPE_FOLDER,
    GRANT_SCOPE_MEDIA_DIR,
    KnownDevice,
    ShareGrant,
    TRUST_STATE_REVOKED,
    TRUST_STATE_TRUSTED,
)
from yaffo.db.repositories import media_dir_repository, p2p_repository
from yaffo.p2p.pairing import PairingError, new_pairing_code
from yaffo.p2p.service import P2PServiceError
from yaffo.p2p.signaling import CallError

pytestmark = pytest.mark.unit

MY_ID = "AAAA-BBBB-CCCC-DDDD"
PEER_ONLINE = "ONLI-NEON-LINE-ONLI"
PEER_OFFLINE = "OFFL-INEO-FFLI-NEOF"
PEER_REVOKED = "REVO-KEDR-EVOK-EDRE"


class FakeP2PService:
    def __init__(self):
        self.identity = SimpleNamespace(device_id=MY_ID, public_key_b64="pubkey")
        self.hub_url = "wss://hub.example"
        self.hub_connected = True
        self.online = {PEER_ONLINE}
        self.accept_result = {"peer_device_id": PEER_ONLINE, "via": "relay"}
        self.accept_error = None
        self.revoke_result = {"revoked": PEER_ONLINE, "peer_notified": True}
        self.revoke_error = None
        self.list_shared_result = {
            "status": "ok",
            "type": "shared_list",
            "files": [
                {
                    "media_dir_id": "remote-lib",
                    "relative_path": "trip/a.jpg",
                    "name": "a.jpg",
                    "size": 12,
                    "sha256": "abc123",
                }
            ],
            "scopes": [],
        }
        self.list_shared_error = None
        self.pull_error = None
        self.accepted_codes = []
        self.revoked_ids = []
        self.listed_ids = []
        self.pulled_files = []

    def connected_device_ids(self):
        return self.online

    def generate_pairing_code(self):
        return new_pairing_code(MY_ID, "pubkey")

    def accept_pairing_code(self, code_text):
        self.accepted_codes.append(code_text)
        if self.accept_error is not None:
            raise self.accept_error
        return self.accept_result

    def revoke_peer(self, device_id):
        self.revoked_ids.append(device_id)
        if self.revoke_error is not None:
            raise self.revoke_error
        return self.revoke_result

    def list_shared(self, device_id):
        self.listed_ids.append(device_id)
        if self.list_shared_error is not None:
            raise self.list_shared_error
        return self.list_shared_result

    def pull_file(self, device_id, media_dir_id, relative_path, destination_root, expected_sha256=None):
        self.pulled_files.append((device_id, media_dir_id, relative_path, destination_root, expected_sha256))
        if self.pull_error is not None:
            raise self.pull_error
        return {"relative_path": "Shared/laptop/trip/a.jpg", "bytes": 12}


@pytest.fixture
def service(app):
    fake = FakeP2PService()
    app.extensions["p2p_service"] = fake
    return fake


def _seed_devices(app):
    with app.app_context():
        db.session.add_all(
            [
                KnownDevice(device_id=PEER_ONLINE, pubkey="k1", display_name="laptop",
                            trust_state=TRUST_STATE_TRUSTED),
                KnownDevice(device_id=PEER_OFFLINE, pubkey="k2", display_name="desktop",
                            trust_state=TRUST_STATE_TRUSTED),
                KnownDevice(device_id=PEER_REVOKED, pubkey="k3", display_name="old-phone",
                            trust_state=TRUST_STATE_REVOKED),
            ]
        )
        db.session.commit()


def _notification(resp):
    payload = json.loads(resp.headers["HX-Trigger"])["showNotification"]
    return payload["message"], payload["type"]


def _devices_changed(resp):
    return "sharingDevicesChanged" in json.loads(resp.headers.get("HX-Trigger", "{}"))


# ---- pages & sidebar ---------------------------------------------------------


def test_sharing_index_redirects_to_settings(client):
    resp = client.get("/sharing")
    assert resp.status_code == 302
    assert "/sharing/settings" in resp.headers["Location"]


def test_settings_page_unavailable_without_service(client):
    body = client.get("/sharing/settings").get_data(as_text=True)
    assert "Device sharing is not running" in body


def test_settings_page_shows_identity_and_presence_tristate(app, client, service):
    _seed_devices(app)
    body = client.get("/sharing/settings").get_data(as_text=True)
    assert MY_ID in body
    assert "wss://hub.example" in body
    assert "laptop" in body and "Online" in body
    assert "desktop" in body and "Offline" in body
    assert "old-phone" in body and "Revoked" in body


def test_presence_unknown_when_hub_unreachable(app, client, service):
    _seed_devices(app)
    service.online = None
    body = client.get("/sharing/settings/section").get_data(as_text=True)
    assert "Unknown" in body
    assert "Online" not in body


def test_sidebar_lists_devices_with_selection(app, client, service):
    _seed_devices(app)
    body = client.get(f"/sharing/sidebar?selected={PEER_ONLINE}").get_data(as_text=True)
    assert "laptop" in body and "desktop" in body and "old-phone" in body
    assert "active" in body
    # Sidebar shows devices even when the engine isn't running (plain DB read).
    del client.application.extensions["p2p_service"]
    assert "laptop" in client.get("/sharing/sidebar").get_data(as_text=True)


def test_device_page_renders(app, client, service):
    _seed_devices(app)
    body = client.get(f"/sharing/devices/{PEER_ONLINE}").get_data(as_text=True)
    assert "laptop" in body
    assert PEER_ONLINE in body
    assert "Shared with this device" in body
    assert "Shared by this device" in body


def test_device_page_shows_grant_controls_when_media_dirs_exist(app, client, service, tmp_path):
    _seed_devices(app)
    with app.app_context():
        media_dir_repository.add_media_dir(db.session, str(tmp_path / "library"))
    body = client.get(f"/sharing/devices/{PEER_ONLINE}").get_data(as_text=True)
    assert "Share a media directory" in body
    assert "Share a folder" in body
    assert "library" in body


def test_device_page_404_for_unknown(client, service):
    assert client.get("/sharing/devices/NOBODY-HOME").status_code == 404


# ---- pairing -----------------------------------------------------------------


def test_generate_pairing_code_fragment(client, service):
    body = client.post("/sharing/pairing-code").get_data(as_text=True)
    assert "data:image/svg+xml" in body  # the QR
    assert "pairing-code-text" in body
    assert "Expires in" in body


def test_pair_success_rerenders_section_with_toast(client, service):
    resp = client.post("/sharing/pair", data={"code": "some-code"})
    assert resp.status_code == 200
    assert service.accepted_codes == ["some-code"]
    message, type_ = _notification(resp)
    assert PEER_ONLINE in message and type_ == "success"
    assert _devices_changed(resp)  # the sidebar refreshes off this
    assert "devices-section" in resp.get_data(as_text=True)


def test_pair_requires_a_code(client, service):
    resp = client.post("/sharing/pair", data={"code": "  "})
    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "Paste a pairing code" in message and type_ == "error"


@pytest.mark.parametrize(
    "error,expected",
    [
        (PairingError("pairing code expired"), "pairing code expired"),
        (CallError("peer did not answer"), "peer did not answer"),
    ],
)
def test_pair_errors_become_toasts_without_swap(client, service, error, expected):
    service.accept_error = error
    resp = client.post("/sharing/pair", data={"code": "bad"})
    assert resp.status_code == 204  # no swap — the pasted code survives
    message, type_ = _notification(resp)
    assert expected in message and type_ == "error"


# ---- revocation & rename -------------------------------------------------------


def test_revoke_notified_toast(app, client, service):
    _seed_devices(app)
    resp = client.post("/sharing/revoke", data={"device_id": PEER_ONLINE})
    assert resp.status_code == 200
    assert service.revoked_ids == [PEER_ONLINE]
    message, _ = _notification(resp)
    assert "was notified" in message
    assert _devices_changed(resp)


def test_revoke_offline_peer_toast(app, client, service):
    _seed_devices(app)
    service.revoke_result = {"revoked": PEER_OFFLINE, "peer_notified": False}
    resp = client.post("/sharing/revoke", data={"device_id": PEER_OFFLINE})
    message, _ = _notification(resp)
    assert "offline" in message


def test_revoke_unknown_device_is_an_error_toast(client, service):
    service.revoke_error = P2PServiceError("NOBODY is not a known device")
    resp = client.post("/sharing/revoke", data={"device_id": "NOBODY"})
    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "not a known device" in message and type_ == "error"


def test_device_page_revoke_rerenders_panel(app, client, service):
    _seed_devices(app)
    resp = client.post(f"/sharing/devices/{PEER_ONLINE}/revoke")
    assert resp.status_code == 200
    assert service.revoked_ids == [PEER_ONLINE]
    assert "device-panel" in resp.get_data(as_text=True)


def test_rename_device(app, client, service):
    _seed_devices(app)
    resp = client.post(f"/sharing/devices/{PEER_ONLINE}/rename", data={"display_name": "kitchen-mac"})
    assert resp.status_code == 200
    assert "kitchen-mac" in resp.get_data(as_text=True)
    assert _devices_changed(resp)
    with app.app_context():
        assert db.session.get(KnownDevice, PEER_ONLINE).display_name == "kitchen-mac"


def test_rename_requires_a_name(app, client, service):
    _seed_devices(app)
    resp = client.post(f"/sharing/devices/{PEER_ONLINE}/rename", data={"display_name": "  "})
    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "cannot be empty" in message and type_ == "error"
    with app.app_context():
        assert db.session.get(KnownDevice, PEER_ONLINE).display_name == "laptop"


# ---- share grants ------------------------------------------------------------


def test_add_media_dir_grant(app, client, service, tmp_path):
    _seed_devices(app)
    with app.app_context():
        media_dir = media_dir_repository.add_media_dir(db.session, str(tmp_path / "library"))

    resp = client.post(
        f"/sharing/devices/{PEER_ONLINE}/grants",
        data={"scope_type": GRANT_SCOPE_MEDIA_DIR, "media_dir_id": media_dir.id},
    )

    assert resp.status_code == 200
    message, type_ = _notification(resp)
    assert message == "Share grant added." and type_ == "success"
    body = resp.get_data(as_text=True)
    assert "Media directory" in body
    assert "library" in body
    with app.app_context():
        grants = p2p_repository.list_active_grants(db.session, PEER_ONLINE)
        assert len(grants) == 1
        assert grants[0].scope_type == GRANT_SCOPE_MEDIA_DIR
        assert grants[0].media_dir_id == media_dir.id


def test_add_folder_grant_stores_relative_path(app, client, service, tmp_path):
    _seed_devices(app)
    root = tmp_path / "library"
    folder = root / "2024" / "summer"
    folder.mkdir(parents=True)
    with app.app_context():
        media_dir = media_dir_repository.add_media_dir(db.session, str(root))

    resp = client.post(
        f"/sharing/devices/{PEER_ONLINE}/grants",
        data={"scope_type": GRANT_SCOPE_FOLDER, "folder_path": str(folder)},
    )

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "2024/summer" in body
    with app.app_context():
        grant = db.session.query(ShareGrant).one()
        assert grant.scope_type == GRANT_SCOPE_FOLDER
        assert grant.media_dir_id == media_dir.id
        assert grant.relative_path == "2024/summer"


def test_add_folder_grant_rejects_paths_outside_media_dirs(app, client, service, tmp_path):
    _seed_devices(app)
    with app.app_context():
        media_dir_repository.add_media_dir(db.session, str(tmp_path / "library"))

    resp = client.post(
        f"/sharing/devices/{PEER_ONLINE}/grants",
        data={"scope_type": GRANT_SCOPE_FOLDER, "folder_path": str(tmp_path / "outside")},
    )

    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "inside a configured media directory" in message and type_ == "error"
    with app.app_context():
        assert p2p_repository.list_active_grants(db.session, PEER_ONLINE) == []


def test_revoke_share_grant(app, client, service, tmp_path):
    _seed_devices(app)
    with app.app_context():
        media_dir = media_dir_repository.add_media_dir(db.session, str(tmp_path / "library"))
        grant = p2p_repository.create_grant(
            db.session,
            PEER_ONLINE,
            GRANT_SCOPE_MEDIA_DIR,
            media_dir_id=media_dir.id,
        )
        grant_id = grant.id

    resp = client.post(f"/sharing/devices/{PEER_ONLINE}/grants/{grant_id}/revoke")

    assert resp.status_code == 200
    message, type_ = _notification(resp)
    assert message == "Share grant revoked." and type_ == "success"
    with app.app_context():
        assert p2p_repository.list_active_grants(db.session, PEER_ONLINE) == []
        assert db.session.get(ShareGrant, grant_id).revoked_at is not None


# ---- receiving shared files --------------------------------------------------


def test_browse_remote_shared_files(app, client, service, tmp_path):
    _seed_devices(app)
    with app.app_context():
        media_dir_repository.add_media_dir(db.session, str(tmp_path / "library"))

    resp = client.post(f"/sharing/devices/{PEER_ONLINE}/shared")

    assert resp.status_code == 200
    assert service.listed_ids == [PEER_ONLINE]
    body = resp.get_data(as_text=True)
    assert "remote-shared-panel" in body
    assert "trip/a.jpg" in body
    assert "Pull" in body


def test_browse_remote_shared_files_error_toast(app, client, service):
    _seed_devices(app)
    service.list_shared_error = CallError("peer did not answer")

    resp = client.post(f"/sharing/devices/{PEER_ONLINE}/shared")

    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "peer did not answer" in message and type_ == "error"


def test_pull_remote_file(app, client, service, tmp_path):
    _seed_devices(app)
    with app.app_context():
        media_dir = media_dir_repository.add_media_dir(db.session, str(tmp_path / "library"))

    resp = client.post(
        f"/sharing/devices/{PEER_ONLINE}/pull",
        data={
            "destination_media_dir_id": media_dir.id,
            "remote_media_dir_id": "remote-lib",
            "relative_path": "trip/a.jpg",
            "sha256": "abc123",
        },
    )

    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "Shared/laptop/trip/a.jpg" in message and type_ == "success"
    assert service.pulled_files == [(PEER_ONLINE, "remote-lib", "trip/a.jpg", media_dir.path, "abc123")]


def test_pull_remote_file_requires_destination_media_dir(app, client, service):
    _seed_devices(app)

    resp = client.post(
        f"/sharing/devices/{PEER_ONLINE}/pull",
        data={"remote_media_dir_id": "remote-lib", "relative_path": "trip/a.jpg"},
    )

    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "Choose a local media directory" in message and type_ == "error"


def test_actions_unavailable_without_service(client):
    for path in (
        "/sharing/pairing-code",
        "/sharing/pair",
        "/sharing/revoke",
        f"/sharing/devices/{PEER_ONLINE}/shared",
        f"/sharing/devices/{PEER_ONLINE}/pull",
    ):
        resp = client.post(path, data={"code": "x", "device_id": "y"})
        assert resp.status_code == 204
        message, type_ = _notification(resp)
        assert "not running" in message and type_ == "error"
