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
import base64
import hashlib
import os
import socket
import threading
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Optional

from sqlalchemy import or_

from yaffo.db import db
from yaffo.db.models import GRANT_SCOPE_FOLDER, GRANT_SCOPE_MEDIA_DIR, MediaItem
from yaffo.db.repositories import media_dir_repository, p2p_repository
from yaffo.logging_config import get_logger
from yaffo.p2p.identity import (
    DeviceIdentity,
    SecretStore,
    device_id_from_pubkey_b64,
    load_or_create_identity,
)
from yaffo.p2p.messages import (
    PeerRecord,
    build_list_shared_request,
    build_pull_file_request,
    build_revocation_notice,
    verify_list_shared_request,
    verify_pull_file_request,
    verify_revocation_notice,
)
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
DEFAULT_PULL_CHUNK_BYTES = 1024 * 1024
MAX_PULL_CHUNK_BYTES = 4 * 1024 * 1024


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
            self._hub_url,
            self.identity,
            self._quic_server,
            on_revoked=self._handle_revocation_notice,
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
        report = self._submit(
            self._hub_client.call(
                peer_device_id,
                payload=payload,
                attempt_upgrade=attempt_upgrade,
            )
        )
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

    def list_shared(self, peer_device_id: str) -> dict:
        """Ask a trusted peer for the files it has granted this device.
        Interactive metadata request: relay response is enough, so it skips
        the direct-upgrade wait."""
        payload = build_list_shared_request(self.identity)
        return self.call(peer_device_id, payload=payload, attempt_upgrade=False)["response"]

    def pull_file_chunk(
        self,
        peer_device_id: str,
        media_dir_id: str,
        relative_path: str,
        offset: int = 0,
        length: int = DEFAULT_PULL_CHUNK_BYTES,
    ) -> dict:
        """Pull one bounded file chunk from a trusted peer. Full transfer
        orchestration/resume lives above this facade; this method owns only
        signed request construction and one QUIC request/response."""
        payload = build_pull_file_request(self.identity, media_dir_id, relative_path, offset, length)
        return self.call(peer_device_id, payload=payload, attempt_upgrade=False)["response"]

    def pull_file(
        self,
        peer_device_id: str,
        media_dir_id: str,
        relative_path: str,
        destination_root: Path,
        expected_sha256: Optional[str] = None,
        chunk_size: int = DEFAULT_PULL_CHUNK_BYTES,
    ) -> dict:
        """Pull one remote file into a local media directory, resuming from a
        `.partial` file if present. The remote path is always nested under a
        stable per-peer folder so two peers with the same relative path don't
        collide."""
        clean_relative_path = self._clean_relative_path(relative_path)
        root = destination_root.expanduser().resolve()
        destination = (root / "Shared" / peer_device_id / clean_relative_path.replace("/", os.sep)).resolve()
        if not self._path_inside(destination, root):
            raise P2PServiceError("destination path escapes the media directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f"{destination.name}.partial")

        offset = partial.stat().st_size if partial.exists() else 0
        size = None
        with partial.open("ab") as handle:
            while True:
                chunk = self.pull_file_chunk(
                    peer_device_id,
                    media_dir_id,
                    clean_relative_path,
                    offset=offset,
                    length=chunk_size,
                )
                data = base64.b64decode(chunk["data_b64"])
                if chunk.get("media_dir_id") != media_dir_id or chunk.get("relative_path") != clean_relative_path:
                    raise P2PServiceError("peer returned a chunk for a different file")
                if chunk.get("offset") != offset:
                    raise P2PServiceError("peer returned a chunk at the wrong offset")
                if len(data) != chunk.get("bytes"):
                    raise P2PServiceError("peer returned a chunk with the wrong byte count")
                if hashlib.sha256(data).hexdigest() != chunk.get("chunk_sha256"):
                    raise P2PServiceError("peer returned a chunk with the wrong checksum")
                if not data and not chunk.get("eof"):
                    raise P2PServiceError("peer returned an empty chunk before the end of the file")

                size = chunk["size"]
                handle.write(data)
                offset = chunk["next_offset"]
                if chunk.get("eof"):
                    break

        actual_sha256 = self._sha256_file(partial)
        if expected_sha256 and actual_sha256 != expected_sha256:
            raise P2PServiceError("downloaded file checksum did not match the peer manifest")
        partial.replace(destination)
        return {
            "saved_to": str(destination),
            "relative_path": str(destination.relative_to(root)),
            "bytes": size if size is not None else offset,
            "sha256": actual_sha256,
        }

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
        """Everything a peer can ask of us over a QUIC stream."""
        kind = body.get("type")
        if kind == "ping":
            return {"status": "ok", "type": "pong", "device_id": self.identity.device_id}
        if kind == "pairing_confirm":
            return self._handle_pairing_confirm(body)
        if kind == "list_shared":
            return self._handle_list_shared(body)
        if kind == "pull_file":
            return self._handle_pull_file(body)
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

    def _peer_lookup(self, session):
        def lookup(device_id: str) -> Optional[PeerRecord]:
            row = p2p_repository.get_known_device(session, device_id)
            return PeerRecord(pubkey=row.pubkey, trust_state=row.trust_state) if row else None

        return lookup

    def _handle_list_shared(self, body: dict) -> dict:
        with self._session() as session:
            denial = verify_list_shared_request(body, self._peer_lookup(session))
            if denial is not None:
                return {"status": "error", "detail": denial}
            peer_device_id = body["device_id"]
            grants = p2p_repository.list_active_grants(session, peer_device_id)
            media_dirs = {entry.id: entry for entry in media_dir_repository.get_media_dir_entries(session)}

            scopes = []
            files = []
            seen: set[tuple[str, str]] = set()
            for grant in grants:
                media_dir = media_dirs.get(grant.media_dir_id)
                if media_dir is None:
                    continue
                root = media_dir.path.expanduser().resolve()
                target = self._grant_target(root, grant)
                if target is None:
                    continue
                scopes.append(
                    {
                        "scope_type": grant.scope_type,
                        "media_dir_id": grant.media_dir_id,
                        "relative_path": grant.relative_path,
                        "name": media_dir.path.name or grant.media_dir_id,
                    }
                )
                for item in self._indexed_media_under(session, target):
                    path = Path(item.full_file_path).expanduser().resolve()
                    if not self._path_inside(path, root) or not path.is_file():
                        continue
                    relative_path = path.relative_to(root).as_posix()
                    key = (grant.media_dir_id, relative_path)
                    if key in seen:
                        continue
                    seen.add(key)
                    files.append(self._file_manifest(grant.media_dir_id, relative_path, path, item))

        return {
            "status": "ok",
            "type": "shared_list",
            "device_id": self.identity.device_id,
            "scopes": scopes,
            "files": files,
        }

    def _handle_pull_file(self, body: dict) -> dict:
        with self._session() as session:
            denial = verify_pull_file_request(body, self._peer_lookup(session))
            if denial is not None:
                return {"status": "error", "detail": denial}

            peer_device_id = body["device_id"]
            media_dir_id = body.get("media_dir_id")
            try:
                relative_path = self._clean_relative_path(body.get("relative_path"))
                offset = self._parse_non_negative_int(body.get("offset"), "offset")
                requested_length = self._parse_non_negative_int(body.get("length"), "length")
            except ValueError as exc:
                return {"status": "error", "detail": str(exc)}
            length = min(requested_length, MAX_PULL_CHUNK_BYTES)
            if length == 0:
                return {"status": "error", "detail": "length must be greater than zero"}

            media_dir = media_dir_repository.media_dir_by_id(session, media_dir_id)
            if media_dir is None:
                return {"status": "error", "detail": "media directory is not configured"}
            root = media_dir.path.expanduser().resolve()
            path = (root / relative_path.replace("/", os.sep)).resolve()
            if not self._path_inside(path, root):
                return {"status": "error", "detail": "path escapes media directory"}
            if not self._grant_allows(session, peer_device_id, media_dir_id, relative_path):
                return {"status": "error", "detail": "no active share grant covers this file"}

            item = session.query(MediaItem).filter_by(full_file_path=str(path)).first()
            if item is None:
                return {"status": "error", "detail": "file is not indexed"}
            if not path.is_file():
                return {"status": "error", "detail": "file is not available"}

            size = path.stat().st_size
            if offset > size:
                return {"status": "error", "detail": "offset is beyond end of file"}
            with path.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read(length)
            next_offset = offset + len(chunk)

        return {
            "status": "ok",
            "type": "file_chunk",
            "device_id": self.identity.device_id,
            "media_dir_id": media_dir_id,
            "relative_path": relative_path,
            "offset": offset,
            "next_offset": next_offset,
            "size": size,
            "eof": next_offset >= size,
            "bytes": len(chunk),
            "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
            "data_b64": base64.b64encode(chunk).decode("ascii"),
        }

    def _grant_target(self, root: Path, grant) -> Optional[Path]:
        if grant.scope_type == GRANT_SCOPE_MEDIA_DIR:
            return root
        if grant.scope_type == GRANT_SCOPE_FOLDER and grant.relative_path:
            target = (root / grant.relative_path.replace("/", os.sep)).resolve()
            return target if self._path_inside(target, root) else None
        return None

    def _indexed_media_under(self, session, path: Path) -> list[MediaItem]:
        path_text = str(path).rstrip("/\\")
        under = f"{path_text}{os.sep}%"
        return (
            session.query(MediaItem)
            .filter(or_(MediaItem.full_file_path == path_text, MediaItem.full_file_path.like(under)))
            .order_by(MediaItem.id)
            .all()
        )

    def _file_manifest(self, media_dir_id: str, relative_path: str, path: Path, item: MediaItem) -> dict:
        stat = path.stat()
        return {
            "media_dir_id": media_dir_id,
            "relative_path": relative_path,
            "name": path.name,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "media_type": item.media_type,
        }

    def _grant_allows(self, session, peer_device_id: str, media_dir_id: str, relative_path: str) -> bool:
        for grant in p2p_repository.list_active_grants(session, peer_device_id):
            if grant.media_dir_id != media_dir_id:
                continue
            if grant.scope_type == GRANT_SCOPE_MEDIA_DIR:
                return True
            if grant.scope_type == GRANT_SCOPE_FOLDER and grant.relative_path:
                prefix = grant.relative_path.strip("/")
                if relative_path == prefix or relative_path.startswith(f"{prefix}/"):
                    return True
        return False

    def _clean_relative_path(self, raw_path) -> str:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("relative_path is required")
        path = PurePosixPath(raw_path.strip())
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise ValueError("relative_path must stay inside the media directory")
        return path.as_posix()

    def _parse_non_negative_int(self, raw_value, field: str) -> int:
        if not isinstance(raw_value, int) or raw_value < 0:
            raise ValueError(f"{field} must be a non-negative integer")
        return raw_value

    def _path_inside(self, path: Path, root: Path) -> bool:
        return path == root or root in path.parents

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _handle_revocation_notice(self, message: dict) -> None:
        """A peer says it revoked us. Only honored when the signature
        verifies against the pubkey we ALREADY hold for that device — an
        unsigned or unverifiable notice from the (hub-forwarded) channel is
        ignored, so nobody can spoof-revoke someone else's pairing.
        Enforcement doesn't depend on this arriving; it exists so the UI can
        say why the peer stopped answering."""
        with self._session() as session:
            if verify_revocation_notice(message, self._peer_lookup(session)) is None:
                p2p_repository.mark_device_revoked(session, message["device_id"])

    def _display_name(self) -> str:
        """What paired peers see this device as; the hostname is the best
        no-configuration default (locally editable on their side)."""
        try:
            return socket.gethostname() or self.identity.device_id
        except OSError:
            return self.identity.device_id
