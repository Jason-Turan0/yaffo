"""The Phase 2 exit-criteria test: two full Yaffo instances (two create_apps,
separate databases and identities) plus a local hub on loopback — pair, call
(relay + direct upgrade), presence, revocation, and re-pairing, end to end
through the P2PService facade the Flask routes will use."""
import re
import socket
import time

import pytest

from yaffo.app import create_app
from yaffo.db import db
from yaffo.db.models import TRUST_STATE_REVOKED, TRUST_STATE_TRUSTED
from yaffo.db.repositories import p2p_repository
from yaffo.p2p.identity import InMemorySecretStore
from yaffo.p2p.lan_discovery import LanCandidate
from yaffo.p2p.pairing import PairingError
from yaffo.p2p.service import P2PService, P2PServiceError
from yaffo.p2p.signaling import CallError

pytestmark = pytest.mark.integration


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeLanDiscovery:
    def __init__(self):
        self.candidates = {}
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def candidate_for(self, device_id):
        return self.candidates.get(device_id)

    def reachable_device_ids(self):
        return set(self.candidates)

    def mark_reachable(self, device_id):
        candidate = self.candidates.get(device_id)
        if candidate is not None:
            self.candidates[device_id] = LanCandidate(
                candidate.device_id,
                candidate.host,
                candidate.port,
                candidate.name,
                time.monotonic(),
            )


def _make_instance(tmp_path, name, hub_url, lan_discovery=None):
    app = create_app(db_path=tmp_path / f"{name}.db", config={"TESTING": True})
    with app.app_context():
        db.create_all()
    port = _free_udp_port()
    lan_discovery = lan_discovery or FakeLanDiscovery()
    service = P2PService(
        app,
        hub_url=hub_url,
        quic_port=port,
        bind_host="127.0.0.1",
        secret_store=InMemorySecretStore(),
        lan_discovery_factory=lambda _identity, _port, _bind_host: lan_discovery,
    )
    service.start()
    app.extensions["p2p_service"] = service  # what _run_web does in production
    return app, service


@pytest.fixture
def two_devices(tmp_path, loopback_hub):
    app_a, service_a = _make_instance(tmp_path, "a", loopback_hub.url)
    app_b, service_b = _make_instance(tmp_path, "b", loopback_hub.url)
    try:
        yield (app_a, service_a), (app_b, service_b)
    finally:
        service_a.stop()
        service_b.stop()
        for app in (app_a, app_b):
            with app.app_context():
                db.session.remove()


def _known_device(app, device_id):
    with app.app_context():
        device = p2p_repository.get_known_device(db.session, device_id)
        if device is None:
            return None
        return {
            "trust_state": device.trust_state,
            "pubkey": device.pubkey,
            "display_name": device.display_name,
            "last_seen_at": device.last_seen_at,
        }


def _pair(service_a, service_b):
    code = service_a.peering.generate_pairing_code()
    return service_b.peering.send_accept_pairing_code(code.encode())


def test_pairing_and_calls_use_lan_when_hub_is_unreachable(tmp_path):
    hub_url = f"ws://127.0.0.1:{_free_tcp_port()}"
    lan_a = FakeLanDiscovery()
    lan_b = FakeLanDiscovery()
    app_a, service_a = _make_instance(tmp_path, "a", hub_url, lan_discovery=lan_a)
    app_b, service_b = _make_instance(tmp_path, "b", hub_url, lan_discovery=lan_b)
    try:
        id_a = service_a.identity.device_id
        id_b = service_b.identity.device_id
        lan_a.candidates[id_b] = LanCandidate(id_b, "127.0.0.1", service_b._quic_port, "b", time.monotonic())
        lan_b.candidates[id_a] = LanCandidate(id_a, "127.0.0.1", service_a._quic_port, "a", time.monotonic())
        initial_b_candidate_updated_at = lan_b.candidates[id_a].updated_at

        result = _pair(service_a, service_b)

        assert result["peer_device_id"] == id_a
        assert result["via"] == "local"
        assert _known_device(app_a, id_b)["trust_state"] == TRUST_STATE_TRUSTED
        assert _known_device(app_b, id_a)["trust_state"] == TRUST_STATE_TRUSTED
        report = service_b.call(id_a)
        assert report["path"] == "local"
        assert report["relay"] is None
        assert report["response"]["type"] == "pong"
        assert lan_b.candidates[id_a].updated_at > initial_b_candidate_updated_at
        assert service_b.local_device_ids() == {id_a}
    finally:
        service_a.stop()
        service_b.stop()
        with app_a.app_context():
            db.session.remove()
        with app_b.app_context():
            db.session.remove()


def test_pair_call_revoke_end_to_end(two_devices, loopback_hub):
    (app_a, service_a), (app_b, service_b) = two_devices
    id_a = service_a.identity.device_id
    id_b = service_b.identity.device_id

    # --- pairing: the confirm rides the relay-first flow. It deliberately
    # skips the punch/direct upgrade because the one-shot trust exchange is
    # complete once the relay response lands.
    result = _pair(service_a, service_b)
    assert result["peer_device_id"] == id_a
    assert result["via"] == "relay"

    # Both sides recorded the other as trusted, with the right pubkeys.
    row_b_on_a = _known_device(app_a, id_b)
    row_a_on_b = _known_device(app_b, id_a)
    assert row_b_on_a["trust_state"] == TRUST_STATE_TRUSTED
    assert row_b_on_a["pubkey"] == service_b.identity.public_key_b64
    assert row_a_on_b["trust_state"] == TRUST_STATE_TRUSTED
    assert row_a_on_b["pubkey"] == service_a.identity.public_key_b64

    # --- presence: both hold open hub WebSockets, so each sees the other
    # online (a set, not None — None would mean "hub unreachable").
    assert id_b in service_a.connected_device_ids()
    assert id_a in service_b.connected_device_ids()

    # --- a relay-first call with the hole-punch upgrade (loopback: lands).
    report = service_b.call(id_a)
    assert report["relay"]["ok"] is True
    assert report["punch"]["ok"] is True
    assert report["direct"]["ok"] is True
    assert report["path"] == "direct"
    assert report["response"]["type"] == "pong"
    # A successful exchange is liveness evidence.
    assert _known_device(app_b, id_a)["last_seen_at"] is not None

    # An interactive request skips the punch wait entirely.
    report = service_b.call(id_a, attempt_upgrade=False)
    assert report["path"] == "relay"
    assert report["punch"] is None

    # --- completed calls close their relay sessions (BYE), so bursts of
    # short calls never pile up against the hub's per-device session cap.
    deadline = time.time() + 2
    while time.time() < deadline and loopback_hub.relay._sessions:
        time.sleep(0.05)
    assert loopback_hub.relay._sessions == {}

    # --- revocation: A flips its local trust store (the enforcement) and
    # the signed courtesy notice flips B's row for A to revoked.
    outcome = service_a.peering.send_revoke_peer(id_b)
    assert outcome["peer_notified"] is True
    assert _known_device(app_a, id_b)["trust_state"] == TRUST_STATE_REVOKED

    deadline = time.time() + 5
    while time.time() < deadline:
        row = _known_device(app_b, id_a)
        if row["trust_state"] == TRUST_STATE_REVOKED:
            break
        time.sleep(0.1)
    assert _known_device(app_b, id_a)["trust_state"] == TRUST_STATE_REVOKED

    # --- re-pairing (fresh code = fresh human consent) heals both sides.
    result = _pair(service_a, service_b)
    assert result["peer_device_id"] == id_a
    assert _known_device(app_a, id_b)["trust_state"] == TRUST_STATE_TRUSTED
    assert _known_device(app_b, id_a)["trust_state"] == TRUST_STATE_TRUSTED


def test_concurrent_calls_to_the_same_peer_all_succeed(two_devices):
    """The remote gallery loads several previews in parallel, so a burst of
    concurrent relay calls to ONE peer must all complete. Regression test for
    the answer-socket collision: the relay tells session sides apart by
    source address, so a callee answering every call from its single server
    socket cross-wires concurrent sessions (only the last-HELLO'd one gets
    its return traffic; the rest time out)."""
    from concurrent.futures import ThreadPoolExecutor

    (_app_a, service_a), (_app_b, service_b) = two_devices
    _pair(service_a, service_b)
    id_a = service_a.identity.device_id

    with ThreadPoolExecutor(max_workers=6) as pool:
        reports = list(pool.map(lambda _: service_b.call(id_a, attempt_upgrade=False), range(6)))

    assert all(report["response"]["type"] == "pong" for report in reports)
    assert all(report["path"] == "relay" for report in reports)


def test_pairing_code_is_single_use(two_devices):
    (_, service_a), (_, service_b) = two_devices
    code = service_a.peering.generate_pairing_code().encode()
    service_b.peering.send_accept_pairing_code(code)
    with pytest.raises(CallError, match="already-used"):
        service_b.peering.send_accept_pairing_code(code)


def test_tampered_code_grants_no_trust(two_devices):
    """A code whose device_id was swapped fails the self-authenticating-ID
    check before anything is dialed (and the cert pin would catch it later
    even if it weren't)."""
    import base64
    import json

    (app_a, service_a), (app_b, service_b) = two_devices
    code = service_a.peering.generate_pairing_code().encode()
    decoded = json.loads(base64.urlsafe_b64decode(code))
    decoded["device_id"] = "EVIL-0000-0000-0000"
    tampered = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode()

    with pytest.raises(PairingError, match="does not match"):
        service_b.peering.send_accept_pairing_code(tampered)
    assert _known_device(app_b, "EVIL-0000-0000-0000") is None
    assert _known_device(app_b, service_a.identity.device_id) is None


def test_call_to_unknown_device_fails_cleanly(two_devices):
    (_, service_a), _ = two_devices
    with pytest.raises(CallError, match="not connected"):
        service_a.call("NOBODY-HOME-0000")


def test_expired_code_is_rejected_locally(two_devices):
    (_, service_a), (_, service_b) = two_devices
    code = service_a.peering.generate_pairing_code()
    code.expires_at = time.time() - 1
    with pytest.raises(PairingError, match="expired"):
        service_b.peering.send_accept_pairing_code(code.encode())


def test_revoking_unknown_device_raises(two_devices):
    (_, service_a), _ = two_devices
    with pytest.raises(P2PServiceError, match="not a known device"):
        service_a.peering.send_revoke_peer("NOBODY-HOME-0000")


def test_pair_and_revoke_entirely_through_the_ui_routes(two_devices):
    """The Phase 3 exit criteria: two instances pair, show each other
    online, and revoke — driven only through the Settings routes the
    browser uses (generate code on A, paste on B, presence badges, revoke
    on A, B's row flips via the signed notice)."""
    (app_a, service_a), (app_b, service_b) = two_devices
    client_a, client_b = app_a.test_client(), app_b.test_client()
    id_a = service_a.identity.device_id
    id_b = service_b.identity.device_id

    # A: generate + display the pairing code (text + QR fragment).
    resp = client_a.post("/sharing/pairing-code")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "data:image/svg+xml" in body
    code = re.search(r'class="pairing-code-text"[^>]*>\s*([^<]+?)\s*</textarea>', body).group(1)

    # B: paste it. The response is the re-rendered section listing A.
    resp = client_b.post("/sharing/pair", data={"code": code})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert "showNotification" in resp.headers.get("HX-Trigger", "")
    assert id_a in resp.get_data(as_text=True)

    # Presence badges: each side's page shows the other device online, and
    # B's sidebar + device page list A.
    assert "presence-online" in client_a.get("/sharing/settings/section").get_data(as_text=True)
    body_b = client_b.get("/sharing/settings").get_data(as_text=True)
    assert id_a in body_b and "presence-online" in body_b
    assert client_b.get(f"/sharing/devices/{id_a}").status_code == 200

    # A revokes B through the UI; A's row flips immediately.
    resp = client_a.post("/sharing/revoke", data={"device_id": id_b})
    assert resp.status_code == 200
    assert "Revoked" in resp.get_data(as_text=True)

    # B's row for A flips once the signed courtesy notice lands.
    deadline = time.time() + 5
    while time.time() < deadline:
        if "Revoked" in client_b.get("/sharing/settings/section").get_data(as_text=True):
            break
        time.sleep(0.1)
    assert "Revoked" in client_b.get("/sharing/settings/section").get_data(as_text=True)
