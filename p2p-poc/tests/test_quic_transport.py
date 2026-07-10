import socket

import pytest

from p2p_poc.identity import load_or_create_identity
from p2p_poc.quic_transport import TransportError, quic_pinned_confirm, start_quic_pairing_server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_quic_confirm_round_trip(tmp_path):
    """Exercises quic_transport.py directly, bypassing FastAPI/HTTP entirely —

    the most direct proof that the pairing exchange is a genuine QUIC
    round trip, not something that happens to work through an abstraction
    layer that could silently be doing something else.
    """
    identity = load_or_create_identity(tmp_path, "127.0.0.1")
    port = _free_port()

    received = {}

    def handler(request: dict) -> dict:
        received.update(request)
        return {"status": "ok", "display_name": "test-server"}

    server = await start_quic_pairing_server("127.0.0.1", port, identity, handler)
    try:
        result = await quic_pinned_confirm(
            "127.0.0.1", port, identity.device_id, {"device_id": "peer-id", "nonce": "abc"}
        )
        assert result == {"status": "ok", "display_name": "test-server"}
        assert received == {"device_id": "peer-id", "nonce": "abc"}
    finally:
        server.close()


async def test_quic_pin_mismatch_is_rejected(tmp_path):
    identity = load_or_create_identity(tmp_path, "127.0.0.1")
    port = _free_port()

    server = await start_quic_pairing_server("127.0.0.1", port, identity, lambda req: {"status": "ok"})
    try:
        with pytest.raises(TransportError, match="certificate mismatch"):
            await quic_pinned_confirm("127.0.0.1", port, "WRONG-DEVICE-ID", {"nonce": "abc"})
    finally:
        server.close()


async def test_quic_server_occupies_udp_not_tcp(tmp_path):
    """The actual proof this is UDP: a TCP listener can coexist on the exact

    same port number (different protocol namespace), while a second UDP
    binding on that port cannot.
    """
    identity = load_or_create_identity(tmp_path, "127.0.0.1")
    port = _free_port()

    server = await start_quic_pairing_server("127.0.0.1", port, identity, lambda req: {"status": "ok"})
    try:
        # A TCP socket on the same port number succeeds — proves the QUIC
        # server did NOT bind TCP here.
        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            tcp_sock.bind(("127.0.0.1", port))
        finally:
            tcp_sock.close()

        # A second UDP socket on the same port fails — proves the QUIC
        # server genuinely holds the UDP port.
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with pytest.raises(OSError):
                udp_sock.bind(("127.0.0.1", port))
        finally:
            udp_sock.close()
    finally:
        server.close()
