import base64
import http.client
import json
import socket
import ssl
import threading
import time

import pytest
import uvicorn

from p2p_poc.main import create_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server(app, host, port, certfile, keyfile):
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


def _unverified_connection(host, port):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return http.client.HTTPSConnection(host, port, timeout=5, context=ctx)


def _post(host, port, path, payload=None):
    conn = _unverified_connection(host, port)
    body = json.dumps(payload) if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    conn.request("POST", path, body=body, headers=headers)
    resp = conn.getresponse()
    data = json.loads(resp.read() or b"{}")
    conn.close()
    return resp.status, data


def _get(host, port, path):
    conn = _unverified_connection(host, port)
    conn.request("GET", path)
    resp = conn.getresponse()
    data = json.loads(resp.read() or b"{}")
    conn.close()
    return resp.status, data


@pytest.fixture
def two_devices(tmp_path):
    """Two full, real HTTPS servers on loopback — the Tier 1 local test from
    docs/development/p2p-sharing.md: separate data dirs (so separate
    keypairs), driving the actual pairing flow end-to-end.
    """
    host = "127.0.0.1"
    port_a, port_b = _free_port(), _free_port()

    app_a = create_app(tmp_path / "a", host, port_a)
    app_b = create_app(tmp_path / "b", host, port_b)
    identity_a = app_a.state.identity
    identity_b = app_b.state.identity

    server_a, thread_a = _run_server(app_a, host, port_a, str(identity_a.cert_path), str(identity_a.key_path))
    server_b, thread_b = _run_server(app_b, host, port_b, str(identity_b.cert_path), str(identity_b.key_path))

    try:
        yield (host, port_a, app_a), (host, port_b, app_b)
    finally:
        server_a.should_exit = True
        server_b.should_exit = True
        thread_a.join(timeout=5)
        thread_b.join(timeout=5)


def test_full_pairing_flow_trusts_both_directions(two_devices):
    (host, port_a, app_a), (host, port_b, app_b) = two_devices

    status, generated = _post(host, port_a, "/api/pairing/generate")
    assert status == 200
    code = generated["code"]

    status, result = _post(host, port_b, "/api/pairing/accept", {"code": code})
    assert status == 200
    assert result["status"] == "ok"
    assert result["peer_device_id"] == app_a.state.identity.device_id

    status, devices_b = _get(host, port_b, "/api/known-devices")
    assert status == 200
    assert any(d["device_id"] == app_a.state.identity.device_id for d in devices_b)

    status, devices_a = _get(host, port_a, "/api/known-devices")
    assert status == 200
    assert any(d["device_id"] == app_b.state.identity.device_id for d in devices_a)


def test_tampered_pairing_code_is_rejected(two_devices):
    """The MITM-caught case: a code claiming a different device_id than the
    cert actually presented at that address must fail the pinning check.
    """
    (host, port_a, app_a), (host, port_b, app_b) = two_devices

    status, generated = _post(host, port_a, "/api/pairing/generate")
    assert status == 200

    decoded = json.loads(base64.urlsafe_b64decode(generated["code"]))
    decoded["device_id"] = "TAMPERED-0000"
    tampered_code = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode()

    status, result = _post(host, port_b, "/api/pairing/accept", {"code": tampered_code})
    assert status == 400
    assert "mismatch" in result["message"].lower() or "certificate" in result["message"].lower()

    status, devices_b = _get(host, port_b, "/api/known-devices")
    assert not any(d["device_id"] == app_a.state.identity.device_id for d in devices_b)


def test_expired_pairing_code_is_rejected(two_devices):
    (host, port_a, app_a), (host, port_b, app_b) = two_devices

    status, generated = _post(host, port_a, "/api/pairing/generate")
    decoded = json.loads(base64.urlsafe_b64decode(generated["code"]))
    decoded["expires_at"] = time.time() - 1
    expired_code = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode()

    status, result = _post(host, port_b, "/api/pairing/accept", {"code": expired_code})
    assert status == 400
    assert "expired" in result["message"].lower()
