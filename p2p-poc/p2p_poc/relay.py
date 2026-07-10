from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from . import stun_client

# A deliberately tiny DERP-style datagram relay (the Tailscale pattern:
# every call starts relayed, then upgrades to a direct path if a hole punch
# lands). This is NOT TURN — no auth, no allocations, no channel framing —
# just enough to prove the relay-first/upgrade flow: two peers announce the
# same session token with a HELLO, and from then on any datagram either
# sends to the relay port is forwarded verbatim to the other. QUIC rides
# over it unmodified, so the exact same pinned-certificate exchange runs
# whether the path is relayed or direct.
#
# The relay port also answers STUN Binding Requests: it already sees each
# peer's NAT-mapped address, so devices can learn their public address from
# the hub itself instead of depending on a third-party STUN server (which
# also lets loopback tests run with no internet at all).

HELLO_MAGIC = b"YRLY1"
ACK_MAGIC = b"YACK1"
TOKEN_BYTES = 16
SESSION_TTL_SECONDS = 600.0


def new_session_token() -> str:
    """Tokens travel as hex inside JSON signaling messages and as raw bytes
    on the wire."""
    return os.urandom(TOKEN_BYTES).hex()


def build_hello(token: str) -> bytes:
    return HELLO_MAGIC + bytes.fromhex(token)


def build_ack(token: str) -> bytes:
    return ACK_MAGIC + bytes.fromhex(token)


def _parse_framed(data: bytes, magic: bytes) -> Optional[str]:
    if len(data) == len(magic) + TOKEN_BYTES and data.startswith(magic):
        return data[len(magic) :].hex()
    return None


def parse_hello(data: bytes) -> Optional[str]:
    return _parse_framed(data, HELLO_MAGIC)


def parse_ack(data: bytes) -> Optional[str]:
    return _parse_framed(data, ACK_MAGIC)


@dataclass
class _Session:
    addrs: list[tuple[str, int]] = field(default_factory=list)
    last_seen: float = 0.0


class RelayProtocol(asyncio.DatagramProtocol):
    """Sessions are keyed by token; sides are identified by their observed
    source address — which is exactly the NAT mapping the peer's later
    datagrams will arrive from, so no other registration is needed.
    """

    def __init__(self) -> None:
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._sessions: dict[str, _Session] = {}
        self._addr_to_token: dict[tuple[str, int], str] = {}

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr) -> None:
        addr = (addr[0], addr[1])

        transaction_id = stun_client.binding_request_transaction_id(data)
        if transaction_id is not None:
            self._transport.sendto(stun_client.build_binding_response(transaction_id, addr), addr)
            return

        token = parse_hello(data)
        if token is not None:
            self._register(token, addr)
            self._transport.sendto(build_ack(token), addr)
            return

        token = self._addr_to_token.get(addr)
        if token is None:
            return  # datagram from an address that never sent a HELLO — drop
        session = self._sessions.get(token)
        if session is None:
            return
        for other in session.addrs:
            if other != addr:
                self._transport.sendto(data, other)

    def _register(self, token: str, addr: tuple[str, int]) -> None:
        self._prune()
        session = self._sessions.setdefault(token, _Session())
        session.last_seen = time.monotonic()
        if addr not in session.addrs:
            if len(session.addrs) >= 2:
                return  # a session is exactly two sides; a third HELLO is bogus
            session.addrs.append(addr)
            self._addr_to_token[addr] = token

    def _prune(self) -> None:
        now = time.monotonic()
        for token, session in list(self._sessions.items()):
            if now - session.last_seen > SESSION_TTL_SECONDS:
                for addr in session.addrs:
                    self._addr_to_token.pop(addr, None)
                del self._sessions[token]


async def start_relay(bind_host: str, port: int) -> tuple[asyncio.DatagramTransport, RelayProtocol]:
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(RelayProtocol, local_addr=(bind_host, port))
    return transport, protocol