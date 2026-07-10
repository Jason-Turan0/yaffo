from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from . import stun

logger = logging.getLogger("yaffo_hub.relay")

# The POC's deliberately tiny DERP-style datagram relay (every call starts
# relayed, then upgrades to a direct path if a hole punch lands), hardened
# for the open internet:
#
#   * HELLOs are only honored for tokens the hub's signaling side just
#     brokered (`authorize()` is called as a connect_request/connect_response
#     pair is forwarded). Random internet UDP cannot create sessions — an
#     unauthorized HELLO is dropped without even an ACK.
#   * Sessions have an idle TTL and a byte cap; forwarded bytes are counted
#     (relay egress is the hub's only meaningful variable cost).
#
# Still deliberately NOT TURN — no RFC 8656 allocations or channel framing.
# Nothing but Yaffo devices ever talks to it, and the datagrams it forwards
# are already end-to-end encrypted QUIC between the two devices.
#
# The relay port also answers STUN Binding Requests (unauthenticated by
# design — address echo, nothing secret): it already sees each peer's
# NAT-mapped address, so devices learn their public address from the hub
# itself, and loopback tests run with no internet at all.

HELLO_MAGIC = b"YRLY1"
ACK_MAGIC = b"YACK1"
TOKEN_BYTES = 16


def new_session_token() -> str:
    """Tokens travel as hex inside JSON signaling messages and as raw bytes
    on the wire. Devices mint them; this helper exists for tests and parity
    with the device side."""
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


def valid_token(token: str) -> bool:
    """Tokens travel as hex inside JSON signaling messages; validate before
    they reach the allowlist so junk can't grow it."""
    if len(token) != TOKEN_BYTES * 2:
        return False
    try:
        bytes.fromhex(token)
        return True
    except ValueError:
        return False


@dataclass
class RelayLimits:
    token_ttl_seconds: float = 60.0
    session_ttl_seconds: float = 600.0
    max_session_bytes: int = 8 * 2**30


@dataclass
class _Session:
    addrs: list[tuple[str, int]] = field(default_factory=list)
    last_seen: float = 0.0
    bytes_forwarded: int = 0
    capped: bool = False


class RelayProtocol(asyncio.DatagramProtocol):
    """Sessions are keyed by token; sides are identified by their observed
    source address — which is exactly the NAT mapping the peer's later
    datagrams will arrive from, so no other registration is needed.
    """

    def __init__(self, limits: Optional[RelayLimits] = None) -> None:
        self._limits = limits or RelayLimits()
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._sessions: dict[str, _Session] = {}
        self._addr_to_token: dict[tuple[str, int], str] = {}
        self._authorized: dict[str, float] = {}  # token -> allowlist expiry
        # Counters surfaced via /healthz; total_bytes_forwarded is the cost
        # signal (relay egress is the only meaningful variable cost).
        self.total_bytes_forwarded = 0
        self.sessions_opened = 0
        self.hellos_rejected = 0
        self.sessions_capped = 0

    # ---- control interface (called by the signaling side) -----------------

    def authorize(self, token: str) -> None:
        """Allowlist a hub-brokered session token: a HELLO for it is honored
        until the entry expires (or indefinitely once its session exists)."""
        self._prune()
        self._authorized[token] = time.monotonic() + self._limits.token_ttl_seconds

    def is_active(self, token: str) -> bool:
        self._prune()
        return token in self._sessions or token in self._authorized

    def stats(self) -> dict:
        self._prune()
        return {
            "active_sessions": len(self._sessions),
            "sessions_opened": self.sessions_opened,
            "total_bytes_forwarded": self.total_bytes_forwarded,
            "hellos_rejected": self.hellos_rejected,
            "sessions_capped": self.sessions_capped,
        }

    # ---- datagram handling -------------------------------------------------

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr) -> None:
        addr = (addr[0], addr[1])

        transaction_id = stun.binding_request_transaction_id(data)
        if transaction_id is not None:
            self._transport.sendto(stun.build_binding_response(transaction_id, addr), addr)
            return

        token = parse_hello(data)
        if token is not None:
            if self._register(token, addr):
                self._transport.sendto(build_ack(token), addr)
            return

        token = self._addr_to_token.get(addr)
        if token is None:
            return  # datagram from an address that never sent a HELLO — drop
        session = self._sessions.get(token)
        if session is None:
            return
        session.last_seen = time.monotonic()
        if session.bytes_forwarded + len(data) > self._limits.max_session_bytes:
            if not session.capped:
                session.capped = True
                self.sessions_capped += 1
                logger.warning("event=session_capped token=%s bytes=%d", token, session.bytes_forwarded)
            return
        for other in session.addrs:
            if other != addr:
                self._transport.sendto(data, other)
                session.bytes_forwarded += len(data)
                self.total_bytes_forwarded += len(data)

    def _register(self, token: str, addr: tuple[str, int]) -> bool:
        self._prune()
        session = self._sessions.get(token)
        if session is None:
            # Only the signaling side can open a session: the token must be
            # on the (short-TTL) allowlist the hub populated while brokering
            # the connect_request/connect_response pair.
            if token not in self._authorized:
                self.hellos_rejected += 1
                return False
            session = self._sessions[token] = _Session()
            self.sessions_opened += 1
        session.last_seen = time.monotonic()
        if addr not in session.addrs:
            if len(session.addrs) >= 2:
                return False  # a session is exactly two sides; a third HELLO is bogus
            session.addrs.append(addr)
            self._addr_to_token[addr] = token
        return True

    def _prune(self) -> None:
        now = time.monotonic()
        for token, expiry in list(self._authorized.items()):
            if now > expiry:
                del self._authorized[token]
        for token, session in list(self._sessions.items()):
            if now - session.last_seen > self._limits.session_ttl_seconds:
                for addr in session.addrs:
                    self._addr_to_token.pop(addr, None)
                del self._sessions[token]


async def start_relay(
    bind_host: str, port: int, limits: Optional[RelayLimits] = None
) -> tuple[asyncio.DatagramTransport, RelayProtocol]:
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: RelayProtocol(limits), local_addr=(bind_host, port)
    )
    return transport, protocol
