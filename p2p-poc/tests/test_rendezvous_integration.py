import base64
import json
import socket
import threading
import time

import pytest
import uvicorn

from p2p_poc.main import create_app
from p2p_poc.rendezvous import create_app as create_rendezvous_app
from tests.test_integration import _get, _post  # reuse the HTTPS test client


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_https_server(app, host, port, certfile, keyfile):
    config = uvicorn.Config(app, host=host, port=port, ssl_certfile=certfile, ssl_keyfile=keyfile, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_started(server)
    return server, thread


def _run_http_server(app, host, port):
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_started(server)
    return server, thread


def _wait_started(server, timeout=5):
    deadline = time.time() + timeout
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("test server failed to start in time")


def _stop(server, thread):
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def rendezvous(tmp_path):
    host = "127.0.0.1"
    port = _free_port()
    app = create_rendezvous_app()
    server, thread = _run_http_server(app, host, port)
    try:
        yield f"http://{host}:{port}"
    finally:
        _stop(server, thread)


def test_pairing_succeeds_despite_a_wrong_address_in_the_code(rendezvous, tmp_path):
    """The actual point of rendezvous: prove it's doing real work, not

    decoration. A registers its real address with rendezvous at startup, but
    the pairing code B actually receives has its host/port tampered with —
    standing in for a NAT'd device that never knew its true reachable address
    to begin with. B, configured with the rendezvous URL, must still succeed
    by resolving A's *real, live* address instead of trusting the code.

    (A is never restarted here, on purpose — an earlier version of this test
    did restart it to simulate "moving," but that also throws away A's
    in-memory pending-pairing state, a separate, already-documented
    limitation. Tampering the code isolates the one thing actually being
    tested: does the lookup get used at all.)
    """
    host = "127.0.0.1"
    dead_port = _free_port()  # a port nothing is listening on

    port_a = _free_port()
    app_a = create_app(tmp_path / "a", host, port_a, rendezvous)
    identity_a = app_a.state.identity
    server_a, thread_a = _run_https_server(app_a, host, port_a, str(identity_a.cert_path), str(identity_a.key_path))

    port_b = _free_port()
    app_b = create_app(tmp_path / "b", host, port_b, rendezvous)
    server_b, thread_b = _run_https_server(app_b, host, port_b, str(app_b.state.identity.cert_path), str(app_b.state.identity.key_path))

    try:
        status, generated = _post(host, port_a, "/api/pairing/generate")
        assert status == 200

        decoded = json.loads(base64.urlsafe_b64decode(generated["code"]))
        assert decoded["port"] == port_a
        decoded["port"] = dead_port  # the address hint is now wrong on purpose
        tampered_code = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode()

        status, result = _post(host, port_b, "/api/pairing/accept", {"code": tampered_code})
        assert status == 200, result
        assert result["status"] == "ok"
        assert result["peer_device_id"] == identity_a.device_id

        status, devices_b = _get(host, port_b, "/api/known-devices")
        assert any(d["device_id"] == identity_a.device_id for d in devices_b)
    finally:
        _stop(server_a, thread_a)
        _stop(server_b, thread_b)


def test_stale_code_fails_without_rendezvous(rendezvous, tmp_path):
    """Control case: the same stale-address scenario, but device B has no

    rendezvous configured — it has only the dead address from the code and
    must fail, proving the success above comes from the lookup, not luck.
    """
    host = "127.0.0.1"

    port_a1 = _free_port()
    app_a1 = create_app(tmp_path / "a", host, port_a1, rendezvous)
    identity_a = app_a1.state.identity
    server_a1, thread_a1 = _run_https_server(app_a1, host, port_a1, str(identity_a.cert_path), str(identity_a.key_path))

    status, generated = _post(host, port_a1, "/api/pairing/generate")
    code = generated["code"]

    _stop(server_a1, thread_a1)  # A is gone and never comes back on this port

    port_b = _free_port()
    app_b = create_app(tmp_path / "b", host, port_b, rendezvous_url=None)  # no rendezvous
    server_b, thread_b = _run_https_server(app_b, host, port_b, str(app_b.state.identity.cert_path), str(app_b.state.identity.key_path))
    try:
        status, result = _post(host, port_b, "/api/pairing/accept", {"code": code})
        assert status == 400
        assert "could not reach peer" in result["message"].lower()
    finally:
        _stop(server_b, thread_b)
