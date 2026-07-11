"""P2PService: the p2p engine as an asyncio loop in a daemon thread inside
the web process.

The loop owns the persistent hub WebSocket (auto-reconnecting), the QUIC/UDP
server socket, and (Phase 5) mDNS. Flask routes call the synchronous facade
methods, which bridge with run_coroutine_threadsafe; the loop calls out to
the DB only through short-lived app-context sessions on its own thread
(WAL + busy_timeout are already configured — no write lock is ever held
across a network exchange).

Also home to the protocol handlers: what a peer can ask of this device over
a QUIC stream (ping, pairing confirm; the grant-checked listing/pull protocol
arrives in Phase 4) and the verified-revocation-notice handler.
"""
from __future__ import annotations

import asyncio
import atexit
import os
import socket
import threading
from contextlib import contextmanager
from typing import Optional

from yaffo.db import db
from yaffo.db.repositories import p2p_repository
from yaffo.logging_config import get_logger
from yaffo.p2p.identity import (
    DeviceIdentity,
    SecretStore,
    device_id_from_pubkey_b64,
    load_or_create_identity,
)
from yaffo.p2p.messages import PeerRecord, build_revocation_notice, verify_revocation_notice
from yaffo.p2p.pairing import (
    PairingCode,
    PairingError,
    new_pairing_code,
    sign_nonce,
    verify_nonce_signature,
)
from yaffo.p2p.quic_transport import PunchAwareQuicServer, start_quic_server
from yaffo.p2p.signaling import HubClient

logger = get_logger(__name__, "webapp")

# The one operated hub (Phase 1), baked in as the app default. Overridable via
# env for dev/tests (deliberately not a user-facing setting yet — multi-hub /
# self-hosting is deferred; see the design doc's non-goals).
DEFAULT_HUB_URL = "wss://hub.yaffo.app"
# Dedicated UDP port for QUIC — the web port belongs to waitress/TCP.
DEFAULT_QUIC_PORT = 5002

FACADE_TIMEOUT_SECONDS = 60.0


def resolve_hub_url() -> str:
    return os.environ.get("YAFFO_HUB_URL") or DEFAULT_HUB_URL


def resolve_quic_port() -> int:
    try:
        return int(os.environ.get("YAFFO_P2P_PORT", ""))
    except ValueError:
        return DEFAULT_QUIC_PORT


class P2PServiceError(Exception):
    pass


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
    ) -> None:
        self._app = flask_app
        self._hub_url = hub_url or resolve_hub_url()
        self._quic_port = quic_port if quic_port is not None else resolve_quic_port()
        logger.info(f"Starting p2p service on {self._quic_port}")
        self._bind_host = bind_host
        self._secret_store = secret_store
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
            self._hub_url, self.identity, self._quic_server, on_revoked=self._handle_revocation_notice
        )
        self._hub_client.start()

    async def _shutdown(self) -> None:
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

    # ---- facade: pairing -----------------------------------------------------

    def generate_pairing_code(self) -> PairingCode:
        code = new_pairing_code(self.identity.device_id, self.identity.public_key_b64)
        with self._pending_lock:
            self._pending[code.nonce] = code
        return code

    def accept_pairing_code(self, code_text: str) -> dict:
        """The joining side of the trust exchange: decode + sanity-check the
        code, deliver the signed confirm over the relay-first call flow, and
        record the peer on success. Raises PairingError / CallError with a
        human-readable reason on failure."""
        code = PairingCode.decode(code_text)
        if code.is_expired():
            raise PairingError("pairing code expired")
        if code.device_id == self.identity.device_id:
            raise PairingError("cannot pair a device with itself")
        # Self-authenticating IDs: a tampered code whose pubkey doesn't hash
        # to its device_id is rejected before anything is dialed.
        if device_id_from_pubkey_b64(code.pubkey) != code.device_id:
            raise PairingError("pairing code device id does not match its public key")

        payload = {
            "type": "pairing_confirm",
            "device_id": self.identity.device_id,
            "pubkey": self.identity.public_key_b64,
            "nonce": code.nonce,
            "signature": sign_nonce(self.identity.private_key, code.nonce),
        }
        # The confirm rides the relay phase of the call (delivered exactly
        # once — the nonce burns on first use); the initiator's certificate
        # is pinned against the code's device_id inside the QUIC handshake,
        # which is what makes the hub trust-irrelevant.
        report = self._submit(self._hub_client.call(code.device_id, payload=payload, attempt_upgrade=False))
        result = report["response"]

        with self._session() as session:
            p2p_repository.upsert_trusted_device(
                session, code.device_id, code.pubkey, result.get("display_name", code.device_id)
            )
        return {"peer_device_id": code.device_id, "via": report["path"]}

    # ---- facade: calls & revocation -------------------------------------------

    def call(self, peer_device_id: str, payload: Optional[dict] = None, attempt_upgrade: bool = True) -> dict:
        """Relay-first call to a paired peer (see HubClient.call). A
        successful exchange is proof the peer is alive, so last_seen_at is
        touched."""
        report = self._submit(
            self._hub_client.call(peer_device_id, payload=payload, attempt_upgrade=attempt_upgrade)
        )
        with self._session() as session:
            p2p_repository.touch_last_seen(session, peer_device_id)
        return report

    def revoke_peer(self, peer_device_id: str) -> dict:
        """Revoke a paired peer: flip the local trust store (the enforcement
        — every incoming request re-checks it) and best-effort deliver a
        signed courtesy notice so the peer's UI can say why. Soft
        revocation: stops future access only."""
        with self._session() as session:
            known = p2p_repository.mark_device_revoked(session, peer_device_id)
        if not known:
            raise P2PServiceError(f"{peer_device_id} is not a known device")

        peer_notified = False
        if self.hub_connected:
            try:
                self._submit(
                    self._hub_client.notify_revoked(peer_device_id, build_revocation_notice(self.identity)),
                    timeout=5.0,
                )
                peer_notified = True  # sent to the hub; delivery to an offline peer is not guaranteed
            except Exception:  # noqa: BLE001 — courtesy only, never blocks enforcement
                pass
        return {"revoked": peer_device_id, "peer_notified": peer_notified}

    # ---- protocol handlers (run on the engine loop) ----------------------------

    def _handle_stream_request(self, body: dict) -> dict:
        """Everything a peer can ask of us over a QUIC stream. Phase 4 adds
        the grant-checked list_shared / pull_file protocol here."""
        kind = body.get("type")
        if kind == "ping":
            return {"status": "ok", "type": "pong", "device_id": self.identity.device_id}
        if kind == "pairing_confirm":
            return self._handle_pairing_confirm(body)
        return {"status": "error", "detail": f"unknown request type: {kind!r}"}

    def _handle_pairing_confirm(self, body: dict) -> dict:
        """The initiating side of the trust exchange: verify the nonce is
        ours (single-use, unexpired) and the joiner's signature proves
        possession of the key behind the identity it claims, then record it.
        The transport already pinned nothing here — it's the payload check
        that authenticates the joiner (A trusting B needs this live proof)."""
        with self._pending_lock:
            code = self._pending.pop(body.get("nonce"), None)
        if code is None:
            return {"status": "error", "detail": "unknown or already-used pairing code"}
        if code.is_expired():
            return {"status": "error", "detail": "pairing code expired"}
        pubkey = body.get("pubkey", "")
        device_id = body.get("device_id", "")
        if device_id_from_pubkey_b64(pubkey) != device_id:
            return {"status": "error", "detail": "device id does not match public key"}
        if not verify_nonce_signature(pubkey, body.get("nonce", ""), body.get("signature", "")):
            return {"status": "error", "detail": "signature verification failed"}

        with self._session() as session:
            p2p_repository.upsert_trusted_device(session, device_id, pubkey, device_id)
        return {"status": "ok", "display_name": self._display_name()}

    def _handle_revocation_notice(self, message: dict) -> None:
        """A peer says it revoked us. Only honored when the signature
        verifies against the pubkey we ALREADY hold for that device — an
        unsigned or unverifiable notice from the (hub-forwarded) channel is
        ignored, so nobody can spoof-revoke someone else's pairing.
        Enforcement doesn't depend on this arriving; it exists so the UI can
        say why the peer stopped answering."""
        with self._session() as session:
            def lookup(device_id: str) -> Optional[PeerRecord]:
                row = p2p_repository.get_known_device(session, device_id)
                return PeerRecord(pubkey=row.pubkey, trust_state=row.trust_state) if row else None

            if verify_revocation_notice(message, lookup) is None:
                p2p_repository.mark_device_revoked(session, message["device_id"])

    def _display_name(self) -> str:
        """What paired peers see this device as; the hostname is the best
        no-configuration default (locally editable on their side)."""
        try:
            return socket.gethostname() or self.identity.device_id
        except OSError:
            return self.identity.device_id
