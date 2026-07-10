"""POC devices against the PRODUCTION hub (yaffo_hub, ../hub) on loopback.

This is the local half of Phase 1's exit criteria: the same client code that
passes the POC-hub integration suite must pair, call, and pull through the
hardened hub — challenge-response WebSocket auth and the brokered-token relay
allowlist included — before it's worth pointing at a real wss:// deployment.

yaffo_hub is deliberately a separate, app-independent package; it's imported
here by path (no install step) since its dependencies are a subset of the
POC's.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

from yaffo_hub.config import HubSettings  # noqa: E402
from yaffo_hub.hub import create_app as create_production_hub_app  # noqa: E402

from p2p_poc.main import create_app as create_device_app

from test_call_integration import _free_port, _free_udp_port, _get, _post, _run_server


@pytest.fixture
def production_hub_and_two_devices(tmp_path):
    """Same loopback topology as test_call_integration's fixture, with the
    hardened hub in the middle instead of the POC hub."""
    host = "127.0.0.1"
    hub_port, relay_port = _free_port(), _free_udp_port()
    port_a, port_b = _free_port(), _free_port()

    settings = HubSettings(host=host, relay_host=host, relay_port=relay_port)
    hub_server, hub_thread = _run_server(create_production_hub_app(settings), host, hub_port)

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


def test_devices_authenticate_and_call_via_production_hub(production_hub_and_two_devices):
    (host, port_a, _), (_, _, app_b) = production_hub_and_two_devices
    peer_id = app_b.state.identity.device_id

    status, report = _post(host, port_a, "/api/call", {"peer_device_id": peer_id, "punch_duration": 5})
    assert status == 200, report
    # The relay phase must succeed — it requires the challenge-response auth
    # AND the brokered-token relay allowlist to have worked end to end.
    assert report["relay"]["ok"] is True
    assert report["response"]["type"] == "pong"


def test_pairing_and_file_pull_via_production_hub(production_hub_and_two_devices):
    (host, port_a, app_a), (_, port_b, app_b) = production_hub_and_two_devices
    id_a, id_b = app_a.state.identity.device_id, app_b.state.identity.device_id

    status, generated = _post(host, port_a, "/api/pairing/generate")
    assert status == 200
    status, result = _post(host, port_b, "/api/pairing/accept", {"code": generated["code"]})
    assert status == 200, result

    status, devices_a = _get(host, port_a, "/api/known-devices")
    assert status == 200
    assert any(d["device_id"] == id_b for d in devices_a)

    status, pulled = _get(host, port_b, f"/api/peers/{id_a}/files")
    assert status == 200, pulled
    assert pulled["files"], "paired peer should be able to pull A's shared seed files"
