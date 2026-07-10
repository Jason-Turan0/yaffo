import http.client
import json
import socket
import ssl
import threading
import time

import pytest
import uvicorn

from p2p_poc.hub import create_app as create_hub_app
from p2p_poc.main import create_app as create_device_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server(app, host, port, certfile=None, keyfile=None):
    config = uvicorn.Config(app, host=host, port=port, ssl_certfile=certfile, ssl_keyfile=keyfile, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 5
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("test server failed to start in time")
    return server, thread


def _request(method, host, port, path, payload=None, timeout=60):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
    body = json.dumps(payload) if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = json.loads(resp.read() or b"{}")
    conn.close()
    return resp.status, data


def _post(host, port, path, payload=None, timeout=60):
    return _request("POST", host, port, path, payload, timeout)


def _get(host, port, path):
    return _request("GET", host, port, path)


def _delete(host, port, path):
    return _request("DELETE", host, port, path)


@pytest.fixture
def hub_and_two_devices(tmp_path):
    """The full relay-first topology on loopback: a hub (WebSocket signaling
    + UDP relay + STUN) and two devices connected to it — no internet, no
    NAT, but every real component in between: WS coordination, STUN via the
    relay port, relayed pinned QUIC, punch, direct pinned QUIC.
    """
    host = "127.0.0.1"
    hub_port, relay_port = _free_port(), _free_udp_port()
    port_a, port_b = _free_port(), _free_port()

    hub_server, hub_thread = _run_server(create_hub_app(host, relay_port), host, hub_port)

    hub_url = f"ws://{host}:{hub_port}"
    app_a = create_device_app(tmp_path / "a", host, port_a, hub_url=hub_url)
    app_b = create_device_app(tmp_path / "b", host, port_b, hub_url=hub_url)
    identity_a, identity_b = app_a.state.identity, app_b.state.identity

    server_a, thread_a = _run_server(app_a, host, port_a, str(identity_a.cert_path), str(identity_a.key_path))
    server_b, thread_b = _run_server(app_b, host, port_b, str(identity_b.cert_path), str(identity_b.key_path))

    try:
        yield (host, port_a, app_a), (host, port_b, app_b)
    finally:
        for server in (server_a, server_b, hub_server):
            server.should_exit = True
        for thread in (thread_a, thread_b, hub_thread):
            thread.join(timeout=5)


def test_call_connects_via_relay_then_upgrades_to_direct(hub_and_two_devices):
    (host, port_a, app_a), (_, _, app_b) = hub_and_two_devices
    peer_id = app_b.state.identity.device_id

    status, report = _post(host, port_a, "/api/call", {"peer_device_id": peer_id, "punch_duration": 5})
    assert status == 200, report
    assert report["status"] == "ok"

    # Phase 1: the relayed path connected and passed the pinned QUIC ping.
    assert report["relay"]["ok"] is True

    # Phase 2: on loopback the punch always lands, so the call must have
    # upgraded to a direct pinned QUIC connection.
    assert report["punch"]["ok"] is True
    assert report["direct"]["ok"] is True
    assert report["path"] == "direct"


def test_call_to_unknown_device_fails_cleanly(hub_and_two_devices):
    (host, port_a, _), _ = hub_and_two_devices

    status, report = _post(host, port_a, "/api/call", {"peer_device_id": "NOBODY-HOME-0000"})
    assert status == 502
    assert "not connected" in report["message"]


def test_pairing_flow_over_hub(hub_and_two_devices):
    """The trust exchange riding the relay-first path: A generates a code,
    B accepts it — with the confirm payload traveling through the hub relay
    inside the pinned QUIC handshake, no direct dial to the code's address.
    Both sides must end up trusting each other.
    """
    (host, port_a, app_a), (_, port_b, app_b) = hub_and_two_devices
    id_a = app_a.state.identity.device_id
    id_b = app_b.state.identity.device_id

    status, generated = _post(host, port_a, "/api/pairing/generate")
    assert status == 200

    status, result = _post(host, port_b, "/api/pairing/accept", {"code": generated["code"]})
    assert status == 200, result
    assert result["status"] == "ok"
    assert result["peer_device_id"] == id_a
    # On loopback the punch lands, so the connect that carried the pairing
    # exchange should also have upgraded — proving the payload flow and the
    # path upgrade coexist.
    assert result["via"] == "direct"

    # Presence: both peers hold open hub WebSockets, so each must see the
    # other as connected (not None — that would mean "no hub / unknown").
    status, devices_b = _get(host, port_b, "/api/known-devices")
    entry_a = next(d for d in devices_b if d["device_id"] == id_a)
    assert entry_a["connected"] is True
    status, devices_a = _get(host, port_a, "/api/known-devices")
    entry_b = next(d for d in devices_a if d["device_id"] == id_b)
    assert entry_b["connected"] is True


def test_file_sharing_with_paired_peer(hub_and_two_devices):
    """The first real data exchange: after pairing, B pulls A's seeded
    text files over the relay path, authorized by B's signed request
    checked against A's trust store.
    """
    (host, port_a, app_a), (_, port_b, app_b) = hub_and_two_devices
    id_a = app_a.state.identity.device_id

    status, mine = _get(host, port_a, "/api/shared-files")
    assert status == 200
    assert len(mine["files"]) == 5
    assert all(f["content"] for f in mine["files"])

    status, generated = _post(host, port_a, "/api/pairing/generate")
    status, result = _post(host, port_b, "/api/pairing/accept", {"code": generated["code"]})
    assert result["status"] == "ok"

    status, pulled = _get(host, port_b, f"/api/peers/{id_a}/files")
    assert status == 200, pulled
    assert pulled["status"] == "ok"
    assert pulled["peer_device_id"] == id_a
    assert pulled["via"] == "relay"  # interactive pulls skip the punch wait
    assert {f["name"] for f in pulled["files"]} == {f["name"] for f in mine["files"]}
    assert all(pf["content"] == mf["content"] for pf, mf in zip(pulled["files"], mine["files"]))


def test_file_pull_from_unpaired_device_is_denied(hub_and_two_devices):
    """No pairing, no files: the serving device must refuse a pull from a
    device that isn't in its trust store, even though both are online and
    the relay path works.
    """
    (host, port_a, app_a), (_, port_b, _) = hub_and_two_devices
    id_a = app_a.state.identity.device_id

    status, result = _get(host, port_b, f"/api/peers/{id_a}/files")
    assert status == 502
    assert "not a trusted device" in result["message"]


def test_revocation_lifecycle(hub_and_two_devices):
    """Pair -> pull works -> A revokes B -> B is denied and sees WHY (its
    row flips to "revoked" via the signed notice) -> re-pairing restores
    access. Revocation is enforced by A's local trust store, not by the
    notice arriving.
    """
    (host, port_a, app_a), (_, port_b, app_b) = hub_and_two_devices
    id_a = app_a.state.identity.device_id
    id_b = app_b.state.identity.device_id

    status, generated = _post(host, port_a, "/api/pairing/generate")
    status, result = _post(host, port_b, "/api/pairing/accept", {"code": generated["code"]})
    assert result["status"] == "ok"
    status, pulled = _get(host, port_b, f"/api/peers/{id_a}/files")
    assert pulled["status"] == "ok"

    status, revoked = _delete(host, port_a, f"/api/known-devices/{id_b}")
    assert status == 200
    assert revoked["peer_notified"] is True

    # A's side: the row is gone entirely.
    status, devices_a = _get(host, port_a, "/api/known-devices")
    assert not any(d["device_id"] == id_b for d in devices_a)

    # B's side: the signed notice arrives over the hub WebSocket
    # asynchronously and flips B's row for A to "revoked".
    deadline = time.time() + 5
    entry_a = None
    while time.time() < deadline:
        status, devices_b = _get(host, port_b, "/api/known-devices")
        entry_a = next((d for d in devices_b if d["device_id"] == id_a), None)
        if entry_a is not None and entry_a["trust_state"] == "revoked":
            break
        time.sleep(0.1)
    assert entry_a is not None and entry_a["trust_state"] == "revoked"

    # Enforcement: B's pulls are now denied by A's trust store.
    status, denied = _get(host, port_b, f"/api/peers/{id_a}/files")
    assert status == 502
    assert "not a trusted device" in denied["message"]

    # Re-pairing (fresh code = fresh human consent) heals both sides.
    status, generated = _post(host, port_a, "/api/pairing/generate")
    status, result = _post(host, port_b, "/api/pairing/accept", {"code": generated["code"]})
    assert result["status"] == "ok"
    status, pulled = _get(host, port_b, f"/api/peers/{id_a}/files")
    assert pulled["status"] == "ok"
    status, devices_b = _get(host, port_b, "/api/known-devices")
    assert next(d for d in devices_b if d["device_id"] == id_a)["trust_state"] == "trusted"


def test_revoking_unknown_device_is_404(hub_and_two_devices):
    (host, port_a, _), _ = hub_and_two_devices
    status, result = _delete(host, port_a, "/api/known-devices/NOBODY-HOME-0000")
    assert status == 404


def test_tampered_code_fails_cleanly_over_hub(hub_and_two_devices):
    """A code claiming a nonexistent device must fail at hub lookup and
    grant no trust. (The certificate-pin MITM case itself is covered by the
    direct-path tests — quic_pinned_request shares _pinned_stream_exchange
    with quic_pinned_confirm, so the pin is the same code on both paths.)
    """
    import base64

    (host, port_a, app_a), (_, port_b, app_b) = hub_and_two_devices

    status, generated = _post(host, port_a, "/api/pairing/generate")
    decoded = json.loads(base64.urlsafe_b64decode(generated["code"]))
    decoded["device_id"] = "EVIL-0000-0000-0000"
    tampered = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode()

    status, result = _post(host, port_b, "/api/pairing/accept", {"code": tampered})
    assert status == 400
    assert "not connected" in result["message"]

    status, devices_b = _get(host, port_b, "/api/known-devices")
    assert not any(d["device_id"] == app_a.state.identity.device_id for d in devices_b)
