"""The pinned QUIC exchange over loopback, and the canary for the private
aioquic API the pin depends on."""
import asyncio
import socket

import pytest

from yaffo.p2p.identity import InMemorySecretStore, load_or_create_identity
from yaffo.p2p.quic_transport import (
    TransportError,
    quic_pinned_request_fresh_socket,
    start_quic_server,
)

pytestmark = pytest.mark.unit


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_aioquic_private_peer_certificate_api_still_exists():
    """Cert pinning reads aioquic's private protocol._quic.tls._peer_certificate
    (no public API exists — see the design doc and the pyproject pin). If an
    aioquic upgrade moves it, THIS test is the loud failure, not a silent
    'peer presented no certificate' in production."""
    from aioquic.tls import Context

    # The QuicConnection.tls attribute is created lazily on connect (the
    # round-trip test below exercises that full chain); the attribute the pin
    # ultimately reads lives on the TLS context class.
    assert hasattr(Context(is_client=True), "_peer_certificate")


async def _serve_and_dial(expected_device_id_override=None):
    identity = load_or_create_identity(InMemorySecretStore())
    port = _free_udp_port()

    def handler(body: dict) -> dict:
        if body.get("type") == "ping":
            return {"status": "ok", "type": "pong", "device_id": identity.device_id}
        return {"status": "error", "detail": "unknown"}

    server = await start_quic_server("127.0.0.1", port, identity, handler)
    try:
        expected = expected_device_id_override or identity.device_id
        return await quic_pinned_request_fresh_socket("127.0.0.1", port, expected, {"type": "ping"})
    finally:
        server.close()


def test_pinned_exchange_round_trip():
    response = asyncio.run(_serve_and_dial())
    assert response["type"] == "pong"


def test_certificate_mismatch_is_rejected():
    """The MITM case: a peer presenting a cert whose fingerprint isn't the
    expected device_id is aborted before any payload flows."""
    with pytest.raises(TransportError, match="certificate mismatch"):
        asyncio.run(_serve_and_dial(expected_device_id_override="EVIL-0000-0000-0000"))


def test_dead_peer_times_out_cleanly():
    """UDP gives no 'connection refused' — a dead peer is silence, so the
    dial must fail by timeout, not hang."""
    with pytest.raises(TransportError, match="could not reach peer"):
        asyncio.run(
            quic_pinned_request_fresh_socket("127.0.0.1", _free_udp_port(), "ANY-0000-0000-0000", {"type": "ping"})
        )
