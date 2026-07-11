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
from yaffo.db.models import KnownDevice, TRUST_STATE_REVOKED, TRUST_STATE_TRUSTED
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
        self.accepted_codes = []
        self.revoked_ids = []

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


def test_actions_unavailable_without_service(client):
    for path in ("/sharing/pairing-code", "/sharing/pair", "/sharing/revoke"):
        resp = client.post(path, data={"code": "x", "device_id": "y"})
        assert resp.status_code == 204
        message, type_ = _notification(resp)
        assert "not running" in message and type_ == "error"
