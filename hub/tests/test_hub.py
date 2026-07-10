import socket
from contextlib import contextmanager

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from yaffo_hub.config import HubSettings
from yaffo_hub.hub import (
    CLOSE_AUTH_FAILED,
    CLOSE_AUTH_TIMEOUT,
    CLOSE_RATE_LIMITED,
    create_app,
)
from yaffo_hub.relay import build_hello, new_session_token, parse_ack

from conftest import FakeDevice, free_udp_port


def _settings(**overrides) -> HubSettings:
    defaults = dict(
        host="127.0.0.1",
        relay_host="127.0.0.1",
        relay_port=free_udp_port(),
        auth_timeout_seconds=0.3,
    )
    defaults.update(overrides)
    return HubSettings(**defaults)


@contextmanager
def connected(client: TestClient, device: FakeDevice):
    """Run the challenge-response handshake, yield the authenticated socket."""
    with client.websocket_connect(f"/ws/{device.device_id}") as ws:
        challenge = ws.receive_json()
        assert challenge["type"] == "challenge"
        ws.send_json(device.auth_reply(challenge["nonce"]))
        info = ws.receive_json()
        assert info["type"] == "hub_info"
        yield ws


def _expect_close(ws, code: int) -> None:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        ws.receive_json()
    assert excinfo.value.code == code


def test_authenticated_device_gets_hub_info_and_presence(device):
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        with connected(client, device):
            assert client.get("/devices").json() == {"connected": [device.device_id]}
            health = client.get("/healthz").json()
            assert health["status"] == "ok"
            assert health["connected_devices"] == 1
            assert health["relay"]["active_sessions"] == 0


def test_bad_signature_is_rejected(device, other_device):
    with TestClient(create_app(_settings())) as client:
        with client.websocket_connect(f"/ws/{device.device_id}") as ws:
            challenge = ws.receive_json()
            # other_device signs — valid signature, wrong key for this ID.
            reply = other_device.auth_reply(challenge["nonce"])
            reply["pubkey"] = device.pubkey_b64
            ws.send_json(reply)
            _expect_close(ws, CLOSE_AUTH_FAILED)
        assert client.get("/devices").json() == {"connected": []}


def test_claiming_someone_elses_device_id_is_rejected(device, other_device):
    """Device-ID squatting: authenticate with a real key under a different
    device's ID."""
    with TestClient(create_app(_settings())) as client:
        with client.websocket_connect(f"/ws/{other_device.device_id}") as ws:
            challenge = ws.receive_json()
            ws.send_json(device.auth_reply(challenge["nonce"]))
            _expect_close(ws, CLOSE_AUTH_FAILED)


def test_silent_client_times_out(device):
    with TestClient(create_app(_settings(auth_timeout_seconds=0.1))) as client:
        with client.websocket_connect(f"/ws/{device.device_id}") as ws:
            ws.receive_json()  # challenge — never answered
            _expect_close(ws, CLOSE_AUTH_TIMEOUT)


def test_forwarding_between_authenticated_devices(device, other_device):
    with TestClient(create_app(_settings())) as client:
        with connected(client, device) as ws_a, connected(client, other_device) as ws_b:
            ws_a.send_json({"type": "hello", "to": other_device.device_id, "note": "hi"})
            message = ws_b.receive_json()
            assert message["note"] == "hi"
            assert message["from"] == device.device_id  # stamped by the hub, not the sender


def test_message_to_offline_device_returns_error(device):
    with TestClient(create_app(_settings())) as client:
        with connected(client, device) as ws:
            ws.send_json({"type": "hello", "to": "NOBO-DYAT-ALLX-XXXX", "token": "t"})
            message = ws.receive_json()
            assert message["type"] == "error"
            assert "not connected" in message["message"]


def _udp_hello(relay_port: int, token: str, timeout: float = 0.5) -> bytes | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(build_hello(token), ("127.0.0.1", relay_port))
        try:
            data, _ = s.recvfrom(2048)
            return data
        except socket.timeout:
            return None


def test_brokered_connect_request_authorizes_relay_token(device, other_device):
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        with connected(client, device) as ws_a, connected(client, other_device) as ws_b:
            token = new_session_token()
            ws_a.send_json({"type": "connect_request", "to": other_device.device_id, "token": token})
            assert ws_b.receive_json()["token"] == token

            assert parse_ack(_udp_hello(settings.relay_port, token)) == token


def test_unbrokered_relay_token_is_rejected(device):
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        with connected(client, device):
            assert _udp_hello(settings.relay_port, new_session_token()) is None


def test_malformed_relay_token_is_not_allowlisted(device, other_device):
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        with connected(client, device) as ws_a, connected(client, other_device):
            ws_a.send_json({"type": "connect_request", "to": other_device.device_id, "token": "junk"})
            message = ws_a.receive_json()
            assert message["type"] == "error"
            assert "malformed" in message["message"]


def test_per_device_session_cap(device, other_device):
    settings = _settings(max_sessions_per_device=2)
    with TestClient(create_app(settings)) as client:
        with connected(client, device) as ws_a, connected(client, other_device) as ws_b:
            for _ in range(2):
                ws_a.send_json(
                    {"type": "connect_request", "to": other_device.device_id, "token": new_session_token()}
                )
                ws_b.receive_json()
            ws_a.send_json(
                {"type": "connect_request", "to": other_device.device_id, "token": new_session_token()}
            )
            message = ws_a.receive_json()
            assert message["type"] == "error"
            assert "too many" in message["message"]


def test_connect_rate_limit(device):
    settings = _settings(connects_per_minute_per_ip=2)
    with TestClient(create_app(settings)) as client:
        with connected(client, device):
            with client.websocket_connect(f"/ws/{device.device_id}") as ws:
                ws.receive_json()  # challenge (2nd connect, still allowed)
            with client.websocket_connect(f"/ws/{device.device_id}") as ws:
                _expect_close(ws, CLOSE_RATE_LIMITED)  # 3rd connect within the window


def test_reconnect_replaces_stale_socket(device):
    with TestClient(create_app(_settings())) as client:
        with connected(client, device):  # goes stale below
            with connected(client, device) as fresh:
                assert client.get("/devices").json() == {"connected": [device.device_id]}
                # The stale socket was closed by the hub; the fresh one still works.
                fresh.send_json({"type": "hello", "to": device.device_id, "note": "self"})
                assert fresh.receive_json()["note"] == "self"
