from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from typing import Optional
from urllib.parse import urlparse

import websockets

from .identity import DeviceIdentity
from .pairing import sign_nonce
from .quic_transport import (
    PunchAwareQuicServer,
    PunchError,
    TransportError,
    resolve_ipv4,
    start_dialer,
)
from .relay import new_session_token

logger = logging.getLogger(__name__)

RECONNECT_DELAY_SECONDS = 2.0
CONNECT_RESPONSE_TIMEOUT_SECONDS = 15.0
CALL_QUIC_TIMEOUT_SECONDS = 5.0
DEFAULT_PUNCH_DURATION_SECONDS = 10.0
# The callee keeps punching a bit longer than the caller asked for — the
# caller only starts its own punch after the relay phase, so the extra
# margin keeps the two send windows overlapping.
CALLEE_PUNCH_GRACE_SECONDS = 5.0


class CallError(Exception):
    pass


class HubClient:
    """Persistent signaling connection to the hub, plus the two halves of

    the relay-first call flow (the Tailscale/DCUtR pattern):

      caller (A)                      hub                       callee (B)
      STUN via relay port  ─┐
      connect_request ──────► forward over B's WebSocket ──────► STUN, relay HELLO
      await response  ◄────── forward over A's WebSocket ◄────── connect_response
                                                                 punch toward A (background)
      relay HELLO, then QUIC ping *via the relay* — the call works from
      here on no matter what the NATs do.
      punch toward B; if it lands, a second QUIC ping *directly* to B's
      NAT-mapped address — same pinned-certificate check on both paths.

    Presence is the WebSocket being open; there is nothing to register and
    nothing to poll. The hub only forwards blobs — every trust decision
    stays end-to-end (fingerprint pinning inside the QUIC handshake).
    """

    def __init__(
        self,
        hub_url: str,
        identity: DeviceIdentity,
        quic_server: PunchAwareQuicServer,
        on_revoked=None,
    ) -> None:
        parsed = urlparse(hub_url)
        if parsed.scheme not in ("ws", "wss"):
            raise ValueError(f"hub URL must be ws:// or wss://, got {hub_url!r}")
        self._hub_url = hub_url.rstrip("/")
        self._hub_http_url = ("https" if parsed.scheme == "wss" else "http") + "://" + parsed.netloc
        self._relay_host: str = parsed.hostname
        self._relay_port: Optional[int] = None  # announced by hub_info on connect
        self._identity = identity
        self._quic_server = quic_server
        self._on_revoked = on_revoked  # callable(message) — verification is the callback's job
        self._ws = None
        self._task: Optional[asyncio.Task] = None
        self._pending: dict[str, asyncio.Future] = {}
        self.connected = False

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def connected_device_ids(self) -> Optional[set[str]]:
        """Presence, straight from the source of truth: the set of device
        IDs currently holding an open signaling WebSocket to the hub. There
        is no registration to go stale — a device is online exactly while
        its socket is. Returns None when the hub itself is unreachable
        (presence *unknown*, which is different from everyone-offline).
        """

        def _fetch() -> dict:
            with urllib.request.urlopen(f"{self._hub_http_url}/devices", timeout=3) as response:
                return json.loads(response.read())

        try:
            data = await asyncio.to_thread(_fetch)
            return set(data.get("connected", []))
        except (OSError, ValueError):
            return None

    async def wait_ready(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.connected and self._relay_port is not None:
                return
            await asyncio.sleep(0.05)
        raise CallError(f"not connected to hub at {self._hub_url} after {timeout}s")

    async def _run(self) -> None:
        url = f"{self._hub_url}/ws/{self._identity.device_id}"
        while True:
            try:
                async with websockets.connect(url) as ws:
                    self._ws = ws
                    self.connected = True
                    logger.info("connected to hub at %s", url)
                    async for raw in ws:
                        try:
                            self._dispatch(json.loads(raw))
                        except (ValueError, KeyError) as exc:
                            logger.warning("ignoring malformed hub message: %s", exc)
            except asyncio.CancelledError:
                raise
            except (OSError, websockets.WebSocketException) as exc:
                logger.warning("hub connection lost (%s), retrying in %.0fs", exc, RECONNECT_DELAY_SECONDS)
            finally:
                self._ws = None
                self.connected = False
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    def _dispatch(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "challenge":
            # The production hub (yaffo_hub) authenticates the socket before
            # forwarding anything: it sends a nonce, we prove possession of
            # the device key (device IDs are self-authenticating hashes of
            # the pubkey, so no registry is involved). The POC hub sends no
            # challenge — this arm simply never fires against it.
            asyncio.ensure_future(
                self._send(
                    {
                        "type": "auth",
                        "pubkey": self._identity.public_key_b64,
                        "signature": sign_nonce(self._identity.private_key, message["nonce"]),
                    }
                )
            )
        elif kind == "hub_info":
            self._relay_port = message["relay_port"]
        elif kind == "connect_request":
            asyncio.ensure_future(self._answer_call(message))
        elif kind in ("connect_response", "error"):
            waiter = self._pending.get(message.get("token"))
            if waiter is not None and not waiter.done():
                waiter.set_result(message)
        elif kind == "revoked":
            if self._on_revoked is not None:
                self._on_revoked(message)
        else:
            logger.warning("ignoring unknown hub message type: %r", kind)

    async def _send(self, message: dict) -> None:
        if self._ws is None:
            raise CallError("hub connection is down")
        await self._ws.send(json.dumps(message))

    async def notify_revoked(self, peer_device_id: str, signed_notice: dict) -> None:
        """Fire-and-forget courtesy delivery of a signed revocation notice.
        Raises CallError only if the hub connection is down; an offline
        peer just never receives it (and discovers the revocation on its
        next denied pull instead) — revocation is enforced locally either
        way.
        """
        await self._send({**signed_notice, "to": peer_device_id})

    # ---- callee side -----------------------------------------------------

    async def _answer_call(self, message: dict) -> None:
        token = message["token"]
        caller = message["from"]
        caller_addr = tuple(message["public"])
        punch_duration = float(message.get("punch_duration", DEFAULT_PUNCH_DURATION_SECONDS))
        upgrade = bool(message.get("upgrade", True))

        try:
            relay_addr = await resolve_ipv4(self._relay_host, self._relay_port)
            my_public = await self._quic_server.discover_public_address(*relay_addr)
            await self._quic_server.relay_hello(relay_addr[0], relay_addr[1], token)
            await self._send(
                {
                    "type": "connect_response",
                    "to": caller,
                    "token": token,
                    "device_id": self._identity.device_id,
                    "public": list(my_public),
                }
            )
        except (TransportError, OSError, asyncio.TimeoutError, CallError) as exc:
            logger.warning("answering call from %s failed: %s", caller, exc)
            try:
                await self._send({"type": "error", "to": caller, "token": token, "message": str(exc)})
            except CallError:
                pass
            return

        if not upgrade:
            return  # caller wants the relay path only (e.g. a quick file pull)

        # Punch toward the caller so their direct dial can traverse our NAT.
        # Failure here is expected on hard NATs and NOT an error for the
        # call — the relay path already works; direct is just the upgrade.
        try:
            result = await self._quic_server.punch(
                caller_addr[0], caller_addr[1], duration=punch_duration + CALLEE_PUNCH_GRACE_SECONDS
            )
            logger.info("punch toward %s succeeded (observed from %s)", caller, result.peer_observed_from)
        except PunchError as exc:
            logger.info("punch toward %s failed, call stays relayed: %s", caller, exc)

    # ---- caller side -----------------------------------------------------

    async def call(
        self,
        peer_device_id: str,
        punch_duration: float = DEFAULT_PUNCH_DURATION_SECONDS,
        payload: Optional[dict] = None,
        attempt_upgrade: bool = True,
    ) -> dict:
        """Run the relay-first flow, delivering `payload` (default: a ping)

        to the peer over the *relay* phase — the path that works no matter
        what the NATs do — inside the pinned QUIC exchange. The peer's
        reply lands in report["response"]. Single-shot payloads (like a
        pairing confirm, whose nonce burns on first use) are therefore
        delivered exactly once; the later direct-upgrade probe always uses
        a plain ping, never re-sends the payload.

        attempt_upgrade=False stops after the relay exchange — the right
        choice for quick request/response interactions (file pulls), where
        waiting out a punch that may never land would just stall the answer
        we already have.
        """
        payload = payload or {"type": "ping"}
        await self.wait_ready()
        relay_addr = await resolve_ipv4(self._relay_host, self._relay_port)
        token = new_session_token()
        report: dict = {
            "peer_device_id": peer_device_id,
            "relay": None,
            "punch": None,
            "direct": None,
            "path": None,
            "response": None,
        }

        dialer = await start_dialer()
        waiter: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[token] = waiter
        try:
            my_public = await dialer.discover_public_address(*relay_addr)
            await self._send(
                {
                    "type": "connect_request",
                    "to": peer_device_id,
                    "token": token,
                    "public": list(my_public),
                    "punch_duration": punch_duration,
                    "upgrade": attempt_upgrade,
                }
            )
            try:
                response = await asyncio.wait_for(waiter, timeout=CONNECT_RESPONSE_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                raise CallError(f"{peer_device_id} did not answer within {CONNECT_RESPONSE_TIMEOUT_SECONDS}s") from None
            if response["type"] == "error":
                raise CallError(response.get("message", "peer reported an error"))
            peer_public = tuple(response["public"])

            # Phase 1 — relay. This must always work: both sides only ever
            # send *outbound* to the relay. The exchange is a full pinned
            # QUIC handshake carrying the payload, so a completed call is
            # already authenticated (and answered) here.
            started = time.monotonic()
            await dialer.relay_hello(relay_addr[0], relay_addr[1], token)
            report["response"] = await dialer.quic_pinned_request(
                relay_addr[0], relay_addr[1], peer_device_id, payload, timeout=CALL_QUIC_TIMEOUT_SECONDS
            )
            report["relay"] = {"ok": True, "rtt_ms": round((time.monotonic() - started) * 1000, 1)}
            report["path"] = "relay"

            if not attempt_upgrade:
                return report

            # Phase 2 — attempt the upgrade. The callee has been punching
            # toward us since it answered; we punch back and, if packets
            # cross, re-run the same pinned exchange directly.
            try:
                punch = await dialer.punch(peer_public[0], peer_public[1], duration=punch_duration)
                report["punch"] = {"ok": True, "peer_observed_from": list(punch.peer_observed_from)}
            except PunchError as exc:
                report["punch"] = {"ok": False, "error": str(exc)}
                return report

            # Dial the address the peer's punch packets *actually arrived
            # from* (ICE calls this a peer-reflexive candidate), not the
            # STUN-advertised one. They differ when the peer sits behind an
            # endpoint-dependent ("hard"/symmetric) NAT — its mapping toward
            # us uses a different port than its mapping toward the STUN
            # server — and the observed address is the one *proven* to have
            # a working mapping back to the peer. This is how one-sided
            # hard NATs still get a direct path (Tailscale does the same).
            direct_addr = punch.peer_observed_from
            try:
                started = time.monotonic()
                await dialer.quic_pinned_request(
                    direct_addr[0], direct_addr[1], peer_device_id, {"type": "ping"}, timeout=CALL_QUIC_TIMEOUT_SECONDS
                )
                report["direct"] = {
                    "ok": True,
                    "address": list(direct_addr),
                    "rtt_ms": round((time.monotonic() - started) * 1000, 1),
                }
                report["path"] = "direct"
            except TransportError as exc:
                report["direct"] = {"ok": False, "error": str(exc)}
            return report
        except (TransportError, OSError, asyncio.TimeoutError) as exc:
            raise CallError(str(exc)) from exc
        finally:
            self._pending.pop(token, None)
            dialer.close()
