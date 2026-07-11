"""Loopback test infrastructure for the p2p engine: an in-process hub
(WebSocket signaling with the production challenge-response auth + the
DERP-style UDP relay + STUN on the relay port) so the Tier-1 integration
tests exercise the full relay-first flow — real crypto, real QUIC, the real
relay — with no internet and no NAT. Mirrors the deployed hub's protocol
(hub/yaffo_hub); the hardened production limits live there, not here.
"""
from __future__ import annotations

import asyncio
import base64
import http
import json
import os
import socket
import threading
import time
from typing import Optional

import pytest
from websockets.asyncio.server import serve

from yaffo.p2p import relay as relay_codec
from yaffo.p2p import stun_client
from yaffo.p2p.identity import device_id_from_pubkey_b64
from yaffo.p2p.pairing import verify_nonce_signature


def free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _LoopbackRelay(asyncio.DatagramProtocol):
    """The POC's tiny relay: sessions keyed by token, sides identified by
    observed source address, datagrams forwarded verbatim to the other side.
    Also answers STUN Binding Requests, so devices STUN against the relay
    port itself (no internet needed)."""

    SESSION_TTL_SECONDS = 600.0

    def __init__(self) -> None:
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._sessions: dict[str, dict] = {}
        self._addr_to_token: dict[tuple[str, int], str] = {}

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr) -> None:
        addr = (addr[0], addr[1])

        transaction_id = stun_client.binding_request_transaction_id(data)
        if transaction_id is not None:
            self._transport.sendto(stun_client.build_binding_response(transaction_id, addr), addr)
            return

        token = relay_codec.parse_hello(data)
        if token is not None:
            self._register(token, addr)
            self._transport.sendto(relay_codec.build_ack(token), addr)
            return

        token = self._addr_to_token.get(addr)
        session = self._sessions.get(token) if token else None
        if session is None:
            return  # datagram from an address that never sent a HELLO — drop
        for other in session["addrs"]:
            if other != addr:
                self._transport.sendto(data, other)

    def _register(self, token: str, addr: tuple[str, int]) -> None:
        session = self._sessions.setdefault(token, {"addrs": [], "last_seen": 0.0})
        session["last_seen"] = time.monotonic()
        if addr not in session["addrs"] and len(session["addrs"]) < 2:
            session["addrs"].append(addr)
            self._addr_to_token[addr] = token


class LoopbackHub:
    """The hub as one background thread with its own asyncio loop: signaling
    (with the production challenge-response device auth), the relay, and a
    /devices HTTP endpoint for presence."""

    def __init__(self) -> None:
        self.host = "127.0.0.1"
        self.port = free_tcp_port()
        self.relay_port = free_udp_port()
        self.connected: dict[str, object] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stop_event: Optional[asyncio.Event] = None

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="loopback-hub", daemon=True)
        self._thread.start()
        assert self._ready.wait(10), "loopback hub failed to start"

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None:
            self._thread.join(10)

    def _run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        transport, _ = await self._loop.create_datagram_endpoint(
            _LoopbackRelay, local_addr=(self.host, self.relay_port)
        )
        async with serve(
            self._handle_device, self.host, self.port, process_request=self._process_request
        ):
            self._ready.set()
            await self._stop_event.wait()
        transport.close()

    def _process_request(self, connection, request):
        if request.path == "/devices":
            return connection.respond(http.HTTPStatus.OK, json.dumps({"connected": sorted(self.connected)}))
        return None  # continue with the WebSocket handshake

    async def _handle_device(self, connection) -> None:
        device_id = connection.request.path.rsplit("/", 1)[-1]

        # Production auth: prove possession of the key whose hash is the
        # claimed device_id (exercises HubClient's challenge arm).
        nonce = base64.urlsafe_b64encode(os.urandom(16)).decode("ascii")
        await connection.send(json.dumps({"type": "challenge", "nonce": nonce}))
        try:
            reply = json.loads(await asyncio.wait_for(connection.recv(), timeout=5))
        except (asyncio.TimeoutError, ValueError):
            await connection.close(code=4401)
            return
        pubkey = str(reply.get("pubkey", ""))
        if (
            reply.get("type") != "auth"
            or device_id_from_pubkey_b64(pubkey) != device_id
            or not verify_nonce_signature(pubkey, nonce, str(reply.get("signature", "")))
        ):
            await connection.close(code=4403)
            return

        self.connected[device_id] = connection
        await connection.send(json.dumps({"type": "hub_info", "relay_port": self.relay_port}))
        try:
            async for raw in connection:
                message = json.loads(raw)
                message["from"] = device_id
                peer = self.connected.get(message.get("to"))
                if peer is None:
                    await connection.send(
                        json.dumps(
                            {
                                "type": "error",
                                "token": message.get("token"),
                                "message": f"{message.get('to')} is not connected to the hub",
                            }
                        )
                    )
                else:
                    await peer.send(json.dumps(message))
        except Exception:  # noqa: BLE001 — a dropped device socket just ends its session
            pass
        finally:
            if self.connected.get(device_id) is connection:
                del self.connected[device_id]


@pytest.fixture
def loopback_hub():
    hub = LoopbackHub()
    hub.start()
    yield hub
    hub.stop()
