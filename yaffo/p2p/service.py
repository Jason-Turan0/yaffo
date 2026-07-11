"""P2PService: the p2p engine as an asyncio loop in a daemon thread inside
the web process.

The loop owns the persistent hub WebSocket (auto-reconnecting), the QUIC/UDP
server socket, and (Phase 5) mDNS. Flask routes call the synchronous facade
methods, which bridge with run_coroutine_threadsafe; the loop calls out to
the DB only through short-lived app-context sessions on its own thread
(WAL + busy_timeout are already configured — no write lock is ever held
across a network exchange).

Inbound QUIC stream requests are dispatched to yaffo.p2p.handlers; this class
keeps lifecycle, presence, and the low-level transport call path. Domain
endpoints (`peering`, `list_shared`, `list_files`, `pull_preview`,
`pull_file`) own request construction and handling.
"""
from __future__ import annotations

import asyncio
import atexit
import inspect
import os
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Optional

from yaffo.db import db
from yaffo.db.repositories import p2p_repository
from yaffo.logging_config import get_logger
from yaffo.p2p.errors import P2PServiceError
from yaffo.p2p.handlers.dispatcher import handle_stream_request
from yaffo.p2p.handlers.list_files import ListFilesEndpoint
from yaffo.p2p.handlers.list_shared import ListSharedEndpoint
from yaffo.p2p.handlers.pairing import PeeringEndpoint
from yaffo.p2p.handlers.ping import PingEndpoint
from yaffo.p2p.handlers.pull_file import PullFileEndpoint
from yaffo.p2p.handlers.pull_preview import PullPreviewEndpoint
from yaffo.p2p.identity import (
    DeviceIdentity,
    SecretStore,
    load_or_create_identity,
)
from yaffo.p2p.lan_discovery import create_lan_discovery
from yaffo.p2p.quic_transport import (
    PunchAwareQuicServer,
    TransportError,
    open_pinned_connection_fresh_socket,
    quic_pinned_request_fresh_socket,
    start_quic_server,
)
from yaffo.p2p.signaling import CallError, HubClient
from yaffo.p2p.transfers import PeerSession, TransferManager

if TYPE_CHECKING:
    from yaffo.p2p.pairing import PairingCode

logger = get_logger(__name__, "webapp")

# The one operated hub (Phase 1), baked in as the app default. Overridable via
# env for dev/tests (deliberately not a user-facing setting yet — multi-hub /
# self-hosting is deferred; see the design doc's non-goals).
DEFAULT_HUB_URL = "wss://hub.yaffo.app"
# Dedicated UDP port for QUIC — the web port belongs to waitress/TCP.
DEFAULT_QUIC_PORT = 5002

FACADE_TIMEOUT_SECONDS = 60.0
LAN_CALL_TIMEOUT_SECONDS = 1.5


def resolve_hub_url() -> str:
    return os.environ.get("YAFFO_HUB_URL") or DEFAULT_HUB_URL


def resolve_quic_port() -> int:
    try:
        return int(os.environ.get("YAFFO_P2P_PORT", ""))
    except ValueError:
        return DEFAULT_QUIC_PORT


def start_p2p_service(flask_app) -> Optional["P2PService"]:
    """Start the sharing engine inside this web process and expose it to the
    routes as app.extensions["p2p_service"]. Failure (port in use, no
    keychain, …) disables sharing for the session but never blocks the app.
    Called from `python -m yaffo`'s web role, and from create_app when
    YAFFO_P2P_ENABLED=1 (the `inv app-local` / `inv start-app` dev flow,
    where there is no __main__ to do it)."""
    try:
        service = P2PService(flask_app)
        service.start()
        flask_app.extensions["p2p_service"] = service
        atexit.register(service.stop)
        return service
    except Exception:
        logger.exception("p2p service failed to start; device sharing disabled")
        return None


class P2PService:
    def __init__(
        self,
        flask_app,
        hub_url: Optional[str] = None,
        quic_port: Optional[int] = None,
        bind_host: str = "0.0.0.0",
        secret_store: Optional[SecretStore] = None,
        lan_discovery_factory=None,
    ) -> None:
        self._app = flask_app
        self._hub_url = hub_url or resolve_hub_url()
        self._quic_port = quic_port if quic_port is not None else resolve_quic_port()
        logger.info(f"Starting p2p service on {self._quic_port}")
        self._bind_host = bind_host
        self._secret_store = secret_store
        self._lan_discovery_factory = lan_discovery_factory or create_lan_discovery
        self._lan_discovery = None
        self.identity: Optional[DeviceIdentity] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._quic_server: Optional[PunchAwareQuicServer] = None
        self._hub_client: Optional[HubClient] = None
        # Pending pairing codes are in-memory by design: a restart
        # invalidating unaccepted codes is acceptable (they expire in ~5 min
        # anyway) and the nonce is the trust anchor, so it never persists.
        self._pending: dict[str, PairingCode] = {}
        self._pending_lock = threading.Lock()
        self._ready = threading.Event()
        self._startup_error: Optional[BaseException] = None
        self.ping = PingEndpoint(self)
        self.peering = PeeringEndpoint(self)
        self.list_shared = ListSharedEndpoint(self)
        self.list_files = ListFilesEndpoint(self)
        self.pull_preview = PullPreviewEndpoint(self)
        self.pull_file = PullFileEndpoint(self)
        self.transfers = TransferManager(self)

    # ---- lifecycle ---------------------------------------------------------

    def start(self, timeout: float = 15.0) -> None:
        """Load the identity (keychain access happens here, on the caller's
        thread — in the interactive web process, where a macOS prompt is
        answerable), then start the engine loop and wait for its sockets."""
        self.identity = load_or_create_identity(self._secret_store)
        self._thread = threading.Thread(target=self._run_loop, name="p2p-engine", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise P2PServiceError("p2p engine did not start in time")
        if self._startup_error is not None:
            raise P2PServiceError(f"p2p engine failed to start: {self._startup_error}") from self._startup_error
        logger.info(f"p2p engine up: device {self.identity.device_id}, hub {self._hub_url}, udp {self._quic_port}")

    def stop(self, timeout: float = 5.0) -> None:
        if self._loop is None or not self._loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result(timeout)
        except Exception:
            logger.exception("p2p engine shutdown raised")
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._startup())
        except BaseException as exc:  # noqa: BLE001 — surfaced to start()
            self._startup_error = exc
            self._ready.set()
            loop.close()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    async def _startup(self) -> None:
        self._quic_server = await start_quic_server(
            self._bind_host, self._quic_port, self.identity, self._handle_stream_request
        )
        self._hub_client = HubClient(
            self._hub_url,
            self.identity,
            self._quic_server,
            on_revoked=self.peering.handle_revocation_notice,
            # Each incoming call is answered from its own ephemeral socket
            # (same cert + handlers) so concurrent relay sessions never share
            # a source address — see HubClient._answer_call.
            session_server_factory=lambda: start_quic_server(
                self._bind_host, 0, self.identity, self._handle_stream_request
            ),
        )
        self._hub_client.start()
        try:
            self._lan_discovery = self._lan_discovery_factory(self.identity, self._quic_port, self._bind_host)
            start_result = self._lan_discovery.start()
            if inspect.isawaitable(start_result):
                await start_result
        except Exception:
            self._lan_discovery = None
            logger.exception("p2p LAN discovery failed to start")

    async def _shutdown(self) -> None:
        if self._lan_discovery is not None:
            stop_result = self._lan_discovery.stop()
            if inspect.isawaitable(stop_result):
                await stop_result
        if self._hub_client is not None:
            await self._hub_client.stop()
        if self._quic_server is not None:
            self._quic_server.close()
        # Punch senders deliberately outlive their call (they run the full
        # send window); reap them so stopping the loop doesn't destroy
        # pending tasks.
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _submit(self, coro, timeout: float = FACADE_TIMEOUT_SECONDS):
        """Bridge a web-thread call onto the engine loop."""
        if self._loop is None:
            raise P2PServiceError("p2p engine is not running")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    @contextmanager
    def _session(self):
        """A short-lived DB session; Flask-SQLAlchemy scopes it to the app
        context and removes it on exit, so nothing leaks across engine-loop
        callbacks."""
        with self._app.app_context():
            yield db.session

    # ---- facade: status ------------------------------------------------------

    @property
    def hub_url(self) -> str:
        return self._hub_url

    @property
    def hub_connected(self) -> bool:
        return self._hub_client is not None and self._hub_client.connected

    def connected_device_ids(self, timeout: float = 5.0) -> Optional[set[str]]:
        """Presence from the hub: the device IDs currently online, or None
        when the hub is unreachable (unknown ≠ everyone-offline)."""
        if self._hub_client is None:
            return None
        return self._submit(self._hub_client.connected_device_ids(), timeout)

    def local_device_ids(self) -> set[str]:
        if self._lan_discovery is None:
            return set()
        return self._lan_discovery.reachable_device_ids()

    # ---- transport calls ------------------------------------------------------

    def call(
        self,
        peer_device_id: str,
        payload: Optional[dict] = None,
        attempt_upgrade: bool = True,
    ) -> dict:
        """Relay-first call to a paired peer (see HubClient.call). A
        successful exchange is proof the peer is alive, so last_seen_at is
        touched."""
        payload_type = (payload or {"type": "ping"}).get("type")
        logger.info(
            "p2p call start peer=%s payload=%s attempt_upgrade=%s",
            peer_device_id,
            payload_type,
            attempt_upgrade,
        )
        report = self._submit(self._call_with_lan_first(peer_device_id, payload=payload, attempt_upgrade=attempt_upgrade))
        logger.info(
            "p2p call done peer=%s payload=%s path=%s relay=%s punch=%s direct=%s",
            peer_device_id,
            payload_type,
            report.get("path"),
            report.get("relay"),
            report.get("punch"),
            report.get("direct"),
        )
        with self._session() as session:
            p2p_repository.touch_last_seen(session, peer_device_id)
        return report

    def expect_ok(self, response: dict) -> dict:
        """Raise if a peer's response to one of our `send`-style calls isn't
        a success — the shared error path for every domain endpoint's
        `send()`."""
        if not isinstance(response, dict) or response.get("status") != "ok":
            detail = response.get("detail", "peer reported an error") if isinstance(response, dict) else "no response"
            raise P2PServiceError(detail)
        return response

    async def _call_with_lan_first(
        self,
        peer_device_id: str,
        payload: Optional[dict] = None,
        attempt_upgrade: bool = True,
    ) -> dict:
        payload = payload or {"type": "ping"}
        local_report = await self._call_lan_candidate(peer_device_id, payload)
        if local_report is not None:
            return local_report
        if self._hub_client is None:
            raise CallError("no LAN path and hub client is not running")
        return await self._hub_client.call(peer_device_id, payload=payload, attempt_upgrade=attempt_upgrade)

    async def open_peer_session(self, peer_device_id: str) -> PeerSession:
        """Open one transfer session to a peer (engine loop only) — LAN
        candidate first (free, fast, zero hub involvement), else the hub's
        relay-first flow with the one-time punch upgrade. The caller owns
        the session and must close() it; TransferManager is the intended
        caller."""
        lan_session = await self._open_lan_session(peer_device_id)
        if lan_session is not None:
            return lan_session
        if self._hub_client is None:
            raise CallError("no LAN path and hub client is not running")
        hub_session = await self._hub_client.open_session(peer_device_id)
        return PeerSession(hub_session.path, hub_session)

    async def _open_lan_session(self, peer_device_id: str) -> Optional[PeerSession]:
        if self._lan_discovery is None:
            return None
        candidate = self._lan_discovery.candidate_for(peer_device_id)
        if candidate is None:
            return None
        try:
            connection = await open_pinned_connection_fresh_socket(
                candidate.host, candidate.port, peer_device_id, timeout=LAN_CALL_TIMEOUT_SECONDS
            )
            ping_reply = await connection.request({"type": "ping"}, timeout=LAN_CALL_TIMEOUT_SECONDS)
            if ping_reply.get("status") != "ok":
                connection.close()
                raise TransportError(ping_reply.get("detail") or "peer rejected the LAN session ping")
        except TransportError as exc:
            logger.info(
                "p2p LAN session candidate failed peer=%s addr=%s:%s: %s; falling back to hub",
                peer_device_id,
                candidate.host,
                candidate.port,
                exc,
            )
            return None
        self._lan_discovery.mark_reachable(peer_device_id)
        return PeerSession("local", connection)

    async def _call_lan_candidate(self, peer_device_id: str, payload: dict) -> Optional[dict]:
        if self._lan_discovery is None:
            return None
        candidate = self._lan_discovery.candidate_for(peer_device_id)
        if candidate is None:
            return None
        started = asyncio.get_running_loop().time()
        try:
            response = await quic_pinned_request_fresh_socket(
                candidate.host,
                candidate.port,
                peer_device_id,
                payload,
                timeout=LAN_CALL_TIMEOUT_SECONDS,
            )
        except TransportError as exc:
            logger.info(
                "p2p LAN candidate failed peer=%s addr=%s:%s: %s; falling back to hub",
                peer_device_id,
                candidate.host,
                candidate.port,
                exc,
            )
            return None
        rtt_ms = round((asyncio.get_running_loop().time() - started) * 1000, 1)
        self._lan_discovery.mark_reachable(peer_device_id)
        return {
            "peer_device_id": peer_device_id,
            "relay": None,
            "punch": None,
            "direct": {"ok": True, "address": [candidate.host, candidate.port], "rtt_ms": rtt_ms},
            "local": {"ok": True, "address": [candidate.host, candidate.port], "rtt_ms": rtt_ms},
            "path": "local",
            "response": response,
        }

    # ---- protocol handlers (run on the engine loop) ----------------------------

    def _handle_stream_request(self, body: dict) -> dict:
        """Everything a peer can ask of us over a QUIC stream."""
        return handle_stream_request(self, body)
