import asyncio
import socket

from p2p_poc import stun_client
from p2p_poc.relay import (
    build_ack,
    build_hello,
    new_session_token,
    parse_ack,
    parse_hello,
    start_relay,
)


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_hello_ack_framing_round_trips():
    token = new_session_token()
    assert parse_hello(build_hello(token)) == token
    assert parse_ack(build_ack(token)) == token
    # Frames are not interchangeable and garbage doesn't parse
    assert parse_hello(build_ack(token)) is None
    assert parse_ack(build_hello(token)) is None
    assert parse_hello(b"nonsense") is None


class _Collector(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.received: asyncio.Queue = asyncio.Queue()

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.received.put_nowait((data, addr))


async def _make_endpoint() -> _Collector:
    loop = asyncio.get_running_loop()
    _, protocol = await loop.create_datagram_endpoint(_Collector, local_addr=("127.0.0.1", 0))
    return protocol


async def test_relay_forwards_between_two_helloed_sides():
    port = _free_udp_port()
    transport, _ = await start_relay("127.0.0.1", port)
    relay_addr = ("127.0.0.1", port)
    try:
        a, b = await _make_endpoint(), await _make_endpoint()
        token = new_session_token()

        a.transport.sendto(build_hello(token), relay_addr)
        data, _ = await asyncio.wait_for(a.received.get(), timeout=2)
        assert parse_ack(data) == token

        b.transport.sendto(build_hello(token), relay_addr)
        data, _ = await asyncio.wait_for(b.received.get(), timeout=2)
        assert parse_ack(data) == token

        a.transport.sendto(b"payload-from-a", relay_addr)
        data, addr = await asyncio.wait_for(b.received.get(), timeout=2)
        assert data == b"payload-from-a"
        assert addr == relay_addr  # relayed traffic arrives *from the relay*, not the peer

        b.transport.sendto(b"payload-from-b", relay_addr)
        data, _ = await asyncio.wait_for(a.received.get(), timeout=2)
        assert data == b"payload-from-b"
    finally:
        transport.close()


async def test_relay_drops_traffic_from_strangers():
    port = _free_udp_port()
    transport, _ = await start_relay("127.0.0.1", port)
    relay_addr = ("127.0.0.1", port)
    try:
        a, stranger = await _make_endpoint(), await _make_endpoint()
        token = new_session_token()

        a.transport.sendto(build_hello(token), relay_addr)
        await asyncio.wait_for(a.received.get(), timeout=2)  # ack

        # A datagram from an address that never sent a HELLO goes nowhere.
        stranger.transport.sendto(b"who-dis", relay_addr)
        await asyncio.sleep(0.2)
        assert a.received.empty()
    finally:
        transport.close()


async def test_relay_answers_stun_binding_requests():
    port = _free_udp_port()
    transport, _ = await start_relay("127.0.0.1", port)
    try:
        a = await _make_endpoint()
        request, transaction_id = stun_client.build_binding_request()
        a.transport.sendto(request, ("127.0.0.1", port))
        data, _ = await asyncio.wait_for(a.received.get(), timeout=2)
        mapped = stun_client.parse_binding_response(data, transaction_id)
        assert mapped == a.transport.get_extra_info("sockname")
    finally:
        transport.close()
