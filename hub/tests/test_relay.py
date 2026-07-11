import asyncio
import os
import struct

from yaffo_hub import stun
from yaffo_hub.relay import (
    RelayLimits,
    build_ack,
    build_bye,
    build_hello,
    new_session_token,
    parse_ack,
    parse_bye,
    parse_hello,
    start_relay,
    valid_token,
)

from conftest import free_udp_port


def test_hello_ack_framing_round_trips():
    token = new_session_token()
    assert parse_hello(build_hello(token)) == token
    assert parse_ack(build_ack(token)) == token
    assert parse_bye(build_bye(token)) == token
    # Frames are not interchangeable and garbage doesn't parse
    assert parse_hello(build_ack(token)) is None
    assert parse_ack(build_hello(token)) is None
    assert parse_bye(build_hello(token)) is None
    assert parse_hello(b"nonsense") is None


def test_token_validation():
    assert valid_token(new_session_token())
    assert not valid_token("shorty")
    assert not valid_token("g" * 32)  # right length, not hex
    assert not valid_token(new_session_token() + "00")


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


async def _relay(limits: RelayLimits = None):
    port = free_udp_port()
    transport, protocol = await start_relay("127.0.0.1", port, limits)
    return transport, protocol, ("127.0.0.1", port)


async def test_unbrokered_hello_is_ignored():
    """Random internet UDP can't open sessions: a HELLO for a token the
    signaling side never brokered gets no ACK and no session."""
    transport, protocol, relay_addr = await _relay()
    try:
        a = await _make_endpoint()
        token = new_session_token()
        a.transport.sendto(build_hello(token), relay_addr)
        await asyncio.sleep(0.2)
        assert a.received.empty()
        assert protocol.stats()["hellos_rejected"] == 1
        assert protocol.stats()["active_sessions"] == 0
    finally:
        transport.close()


async def test_authorized_session_forwards_both_ways():
    transport, protocol, relay_addr = await _relay()
    try:
        a, b = await _make_endpoint(), await _make_endpoint()
        token = new_session_token()
        protocol.authorize(token)

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

        stats = protocol.stats()
        assert stats["sessions_opened"] == 1
        assert stats["total_bytes_forwarded"] == len(b"payload-from-a") + len(b"payload-from-b")
    finally:
        transport.close()


async def test_expired_authorization_no_longer_admits_hellos():
    transport, protocol, relay_addr = await _relay(RelayLimits(token_ttl_seconds=0.1))
    try:
        a = await _make_endpoint()
        token = new_session_token()
        protocol.authorize(token)
        await asyncio.sleep(0.3)
        a.transport.sendto(build_hello(token), relay_addr)
        await asyncio.sleep(0.2)
        assert a.received.empty()
    finally:
        transport.close()


async def test_second_side_can_join_after_allowlist_expiry():
    """The caller HELLOs only after the connect_response round trip, which
    can outlive the allowlist TTL — joining an *existing* session must not
    depend on the allowlist entry still being there."""
    transport, protocol, relay_addr = await _relay(RelayLimits(token_ttl_seconds=0.1))
    try:
        a, b = await _make_endpoint(), await _make_endpoint()
        token = new_session_token()
        protocol.authorize(token)
        a.transport.sendto(build_hello(token), relay_addr)
        await asyncio.wait_for(a.received.get(), timeout=2)  # ack

        await asyncio.sleep(0.3)  # allowlist entry is gone, session is not
        b.transport.sendto(build_hello(token), relay_addr)
        data, _ = await asyncio.wait_for(b.received.get(), timeout=2)
        assert parse_ack(data) == token
    finally:
        transport.close()


async def test_session_byte_cap_stops_forwarding():
    transport, protocol, relay_addr = await _relay(RelayLimits(max_session_bytes=10))
    try:
        a, b = await _make_endpoint(), await _make_endpoint()
        token = new_session_token()
        protocol.authorize(token)
        for side in (a, b):
            side.transport.sendto(build_hello(token), relay_addr)
            await asyncio.wait_for(side.received.get(), timeout=2)  # ack

        a.transport.sendto(b"0123456789", relay_addr)  # exactly the cap
        data, _ = await asyncio.wait_for(b.received.get(), timeout=2)
        assert data == b"0123456789"

        a.transport.sendto(b"x", relay_addr)  # over the cap — dropped
        await asyncio.sleep(0.2)
        assert b.received.empty()
        assert protocol.stats()["sessions_capped"] == 1
    finally:
        transport.close()


async def test_bye_from_a_session_side_closes_it_immediately():
    """A completed call must free its session (and its is_active claim on
    the signaling side's per-device cap) without waiting out the idle TTL."""
    transport, protocol, relay_addr = await _relay()
    try:
        a, b = await _make_endpoint(), await _make_endpoint()
        token = new_session_token()
        protocol.authorize(token)
        for side in (a, b):
            side.transport.sendto(build_hello(token), relay_addr)
            await asyncio.wait_for(side.received.get(), timeout=2)  # ack
        assert protocol.is_active(token)

        a.transport.sendto(build_bye(token), relay_addr)
        await asyncio.sleep(0.2)
        assert not protocol.is_active(token)
        assert protocol.stats()["active_sessions"] == 0
        assert protocol.stats()["sessions_closed"] == 1

        # The closed session forwards nothing anymore.
        b.transport.sendto(b"late-payload", relay_addr)
        await asyncio.sleep(0.2)
        assert a.received.empty()
    finally:
        transport.close()


async def test_bye_from_a_stranger_is_ignored():
    """Knowing a token is not enough — only a registered side of the session
    may close it."""
    transport, protocol, relay_addr = await _relay()
    try:
        a, stranger = await _make_endpoint(), await _make_endpoint()
        token = new_session_token()
        protocol.authorize(token)
        a.transport.sendto(build_hello(token), relay_addr)
        await asyncio.wait_for(a.received.get(), timeout=2)  # ack

        stranger.transport.sendto(build_bye(token), relay_addr)
        await asyncio.sleep(0.2)
        assert protocol.is_active(token)
        assert protocol.stats()["active_sessions"] == 1
    finally:
        transport.close()


async def test_relay_drops_traffic_from_strangers():
    transport, protocol, relay_addr = await _relay()
    try:
        a, stranger = await _make_endpoint(), await _make_endpoint()
        token = new_session_token()
        protocol.authorize(token)
        a.transport.sendto(build_hello(token), relay_addr)
        await asyncio.wait_for(a.received.get(), timeout=2)  # ack

        # A datagram from an address that never sent a HELLO goes nowhere.
        stranger.transport.sendto(b"who-dis", relay_addr)
        await asyncio.sleep(0.2)
        assert a.received.empty()
    finally:
        transport.close()


async def test_relay_answers_stun_binding_requests_without_auth():
    """STUN stays unauthenticated by design — address echo, nothing secret."""
    transport, _, relay_addr = await _relay()
    try:
        a = await _make_endpoint()
        # Build a Binding Request by hand (the hub only ships the responder half).
        transaction_id = os.urandom(12)
        request = struct.pack(">HHI", stun.BINDING_REQUEST, 0, stun.STUN_MAGIC_COOKIE) + transaction_id
        a.transport.sendto(request, relay_addr)
        data, _ = await asyncio.wait_for(a.received.get(), timeout=2)
        assert stun.binding_request_transaction_id(data) is None  # it's a response
        assert data[8:20] == transaction_id
    finally:
        transport.close()
