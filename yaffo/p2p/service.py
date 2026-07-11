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

from sqlalchemy import func, or_

from yaffo.db import db
from yaffo.db.models import (
    GRANT_SCOPE_FOLDER,
    GRANT_SCOPE_MEDIA_DIR,
    MEDIA_TYPE_PHOTO,
    MEDIA_TYPE_VIDEO,
    ClassificationLabel,
    Face,
    MediaItem,
    MediaLabel,
    Person,
    PersonFace,
    Tag,
)
from yaffo.db.repositories import media_dir_repository, p2p_repository
from yaffo.db.repositories.media_filter_repository import apply_media_filters
from yaffo.utils.image import preview_jpeg_bytes
from yaffo.logging_config import get_logger
from yaffo.p2p.identity import (
    DeviceIdentity,
    SecretStore,
    device_id_from_pubkey_b64,
    load_or_create_identity,
)
from yaffo.p2p.messages import (
    PeerRecord,
    build_list_files_request,
    build_list_shared_request,
    build_pull_file_request,
    build_pull_preview_request,
    build_revocation_notice,
    verify_list_files_request,
    verify_list_shared_request,
    verify_pull_file_request,
    verify_pull_preview_request,
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
DEFAULT_LIST_FILES_LIMIT = 50
MAX_LIST_FILES_LIMIT = 200
DEFAULT_PREVIEW_DIMENSION = 512
MAX_PREVIEW_DIMENSION = 1024
# The filter criteria a peer may send with list_files — the full home-page
# filter vocabulary. Plain values mean the same thing on both devices;
# people/labels are SERVING-side entity IDs the peer learned from this
# device's own facets and is echoing back (never the requester's local IDs).
# Unknown keys are rejected, not ignored, so a filter the serving side
# doesn't understand can't silently widen results.
def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int_list(value) -> bool:
    return isinstance(value, list) and all(_is_int(item) for item in value)


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


LIST_FILES_FILTER_VALIDATORS = {
    "path": lambda v: isinstance(v, str),
    "media_type": lambda v: v in (MEDIA_TYPE_PHOTO, MEDIA_TYPE_VIDEO),
    "year": _is_int,
    "month": _is_int,
    "device": lambda v: isinstance(v, str),
    "favorite": lambda v: isinstance(v, (bool, int)),
    "gender": _is_int,
    "people": _is_int_list,
    "person_match_type": lambda v: v in ("any", "all"),
    "labels": _is_int_list,
    "labels_match_type": lambda v: v in ("any", "all"),
    "tag_name": lambda v: isinstance(v, str),
    "tag_value": lambda v: isinstance(v, str),
    "locations": _is_str_list,
    "location_match_type": lambda v: v in ("any", "all"),
    "unnamed": lambda v: isinstance(v, (bool, int)),
    "proximity_lat": _is_number,
    "proximity_lon": _is_number,
    "proximity_km": _is_number,
}
LIST_FILES_FILTER_KEYS = tuple(LIST_FILES_FILTER_VALIDATORS)


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
            # Each incoming call is answered from its own ephemeral socket
            # (same cert + handlers) so concurrent relay sessions never share
            # a source address — see HubClient._answer_call.
            session_server_factory=lambda: start_quic_server(
                self._bind_host, 0, self.identity, self._handle_stream_request
            ),
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
        """Ask a trusted peer for the scopes it grants this device, each with
        a file count — file listings are paginated separately through
        list_shared_files. Interactive metadata request: relay response is
        enough, so it skips the direct-upgrade wait."""
        payload = build_list_shared_request(self.identity)
        return self._expect_ok(self.call(peer_device_id, payload=payload, attempt_upgrade=False)["response"])

    def list_shared_files(
        self,
        peer_device_id: str,
        media_dir_id: str,
        relative_path: str = "",
        filters: Optional[dict] = None,
        offset: int = 0,
        limit: int = DEFAULT_LIST_FILES_LIMIT,
    ) -> dict:
        """One page of file manifests for a granted scope on a trusted peer,
        newest first, filtered by the plain-value criteria in `filters` (see
        LIST_FILES_FILTER_KEYS). The response carries facets (years/devices
        within the scope) for building filter UIs."""
        payload = build_list_files_request(
            self.identity, media_dir_id, relative_path, filters or {}, offset, limit
        )
        return self._expect_ok(self.call(peer_device_id, payload=payload, attempt_upgrade=False)["response"])

    def pull_preview(
        self,
        peer_device_id: str,
        media_dir_id: str,
        relative_path: str,
        max_dimension: int = DEFAULT_PREVIEW_DIMENSION,
    ) -> bytes:
        """A downscaled JPEG preview of one shared file (the peer compresses
        before sending — the gallery never pulls originals)."""
        payload = build_pull_preview_request(self.identity, media_dir_id, relative_path, max_dimension)
        response = self._expect_ok(self.call(peer_device_id, payload=payload, attempt_upgrade=False)["response"])
        return base64.b64decode(response["data_b64"])

    def _expect_ok(self, response: dict) -> dict:
        """A peer's application-level refusal (bad scope, revoked grant, …)
        surfaces like any other call failure instead of leaking an error
        body to the caller."""
        if not isinstance(response, dict) or response.get("status") != "ok":
            detail = response.get("detail", "peer reported an error") if isinstance(response, dict) else "no response"
            raise P2PServiceError(detail)
        return response

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
        return self._expect_ok(self.call(peer_device_id, payload=payload, attempt_upgrade=False)["response"])

    def pull_file(
        self,
        peer_device_id: str,
        media_dir_id: str,
        relative_path: str,
        destination_root: Path,
        expected_sha256: Optional[str] = None,
        chunk_size: int = DEFAULT_PULL_CHUNK_BYTES,
        destination_device_name: Optional[str] = None,
        destination_collection_path: Optional[str] = None,
        source_scope_path: Optional[str] = None,
    ) -> dict:
        """Pull one remote file into the configured download directory,
        resuming from a `.partial` file if present."""
        clean_relative_path = self._clean_relative_path(relative_path)
        root = destination_root.expanduser().resolve()
        device_folder = self._safe_path_component(destination_device_name or peer_device_id, peer_device_id)
        collection_folder = self._clean_destination_path(destination_collection_path or media_dir_id)
        file_path = self._relative_path_inside_scope(clean_relative_path, source_scope_path)
        destination = (
            root
            / device_folder
            / collection_folder.replace("/", os.sep)
            / file_path.replace("/", os.sep)
        ).resolve()
        if not self._path_inside(destination, root):
            raise P2PServiceError("destination path escapes the download directory")
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
        if kind == "list_files":
            return self._handle_list_files(body)
        if kind == "pull_preview":
            return self._handle_pull_preview(body)
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
        """The scopes this peer holds grants for, each with a count of the
        indexed files under it. No file manifests here — those are paged out
        through list_files, so a huge granted library never rides one
        response."""
        with self._session() as session:
            denial = verify_list_shared_request(body, self._peer_lookup(session))
            if denial is not None:
                return {"status": "error", "detail": denial}
            peer_device_id = body["device_id"]
            grants = p2p_repository.list_active_grants(session, peer_device_id)
            media_dirs = {entry.id: entry for entry in media_dir_repository.get_media_dir_entries(session)}

            scopes = []
            seen: set[tuple[str, str]] = set()
            for grant in grants:
                media_dir = media_dirs.get(grant.media_dir_id)
                if media_dir is None:
                    continue
                root = media_dir.path.expanduser().resolve()
                target = self._grant_target(root, grant)
                if target is None:
                    continue
                key = (grant.media_dir_id, grant.relative_path or "")
                if key in seen:
                    continue
                seen.add(key)
                scopes.append(
                    {
                        "scope_type": grant.scope_type,
                        "media_dir_id": grant.media_dir_id,
                        "relative_path": grant.relative_path,
                        "name": media_dir.path.name or grant.media_dir_id,
                        "file_count": self._indexed_media_query(session, target).count(),
                    }
                )

        return {
            "status": "ok",
            "type": "shared_list",
            "device_id": self.identity.device_id,
            "scopes": scopes,
        }

    def _resolve_scoped_request(self, session, body: dict):
        """Shared validation for scope-addressed requests (list_files,
        pull_preview): clean the scope path, resolve it inside a configured
        media dir, and check an active grant covers it. Returns
        (error_response | None, media_dir_id, scope_path, root, target)."""
        peer_device_id = body["device_id"]
        media_dir_id = body.get("media_dir_id")
        raw_scope = body.get("relative_path") or ""
        if not isinstance(raw_scope, str):
            return {"status": "error", "detail": "relative_path must be a string"}, None, None, None, None
        try:
            scope_path = self._clean_relative_path(raw_scope) if raw_scope.strip() else ""
        except ValueError as exc:
            return {"status": "error", "detail": str(exc)}, None, None, None, None
        media_dir = media_dir_repository.media_dir_by_id(session, media_dir_id)
        if media_dir is None:
            return {"status": "error", "detail": "media directory is not configured"}, None, None, None, None
        root = media_dir.path.expanduser().resolve()
        target = (root / scope_path.replace("/", os.sep)).resolve() if scope_path else root
        if not self._path_inside(target, root):
            return {"status": "error", "detail": "path escapes media directory"}, None, None, None, None
        if not self._grant_allows(session, peer_device_id, media_dir_id, scope_path):
            return {"status": "error", "detail": "no active share grant covers this scope"}, None, None, None, None
        return None, media_dir_id, scope_path, root, target

    def _handle_list_files(self, body: dict) -> dict:
        """One page of file manifests for a scope the peer was granted,
        newest first. The requested scope must sit inside an active grant;
        the grant is applied FIRST, then the request's filters — and both
        the filters and pagination run in SQL, so browsing a large library
        stays a metadata request. Responds with facets (years/devices within
        the scope) so the peer can build its filter UI."""
        with self._session() as session:
            denial = verify_list_files_request(body, self._peer_lookup(session))
            if denial is not None:
                return {"status": "error", "detail": denial}
            error, media_dir_id, scope_path, root, target = self._resolve_scoped_request(session, body)
            if error is not None:
                return error
            try:
                offset = self._parse_non_negative_int(body.get("offset"), "offset")
                limit = self._parse_non_negative_int(body.get("limit"), "limit")
            except ValueError as exc:
                return {"status": "error", "detail": str(exc)}
            limit = min(limit, MAX_LIST_FILES_LIMIT)
            if limit == 0:
                return {"status": "error", "detail": "limit must be greater than zero"}
            filters = body.get("filters") or {}
            if not isinstance(filters, dict):
                return {"status": "error", "detail": "filters must be an object"}
            unknown = set(filters) - set(LIST_FILES_FILTER_KEYS)
            if unknown:
                return {"status": "error", "detail": f"unknown filters: {', '.join(sorted(unknown))}"}
            for key, value in filters.items():
                if not LIST_FILES_FILTER_VALIDATORS[key](value):
                    return {"status": "error", "detail": f"invalid value for filter {key!r}"}

            scope_query = self._indexed_media_query(session, target)
            files_query = self._apply_list_filters(session, scope_query, target, filters)
            total = files_query.count()
            files = []
            page = (
                files_query.order_by(MediaItem.date_taken.desc(), MediaItem.full_file_path)
                .offset(offset)
                .limit(limit)
            )
            for item in page:
                path = Path(item.full_file_path).expanduser().resolve()
                if not self._path_inside(path, root) or not path.is_file():
                    continue
                files.append(self._file_manifest(media_dir_id, path.relative_to(root).as_posix(), path, item))
            facets = self._list_files_facets(session, scope_query, filters)

        return {
            "status": "ok",
            "type": "shared_files",
            "device_id": self.identity.device_id,
            "media_dir_id": media_dir_id,
            "relative_path": scope_path,
            "filters": filters,
            "offset": offset,
            "limit": limit,
            "total": total,
            "files": files,
            "facets": facets,
        }

    def _apply_list_filters(self, session, files_query, target: Path, filters: dict):
        """The request's criteria (already validated), applied on top of the
        grant-scoped query. Everything except `path` shares the home
        gallery's SQL semantics via apply_media_filters; `path` is matched
        against the path *relative to the scope* so a term can never
        accidentally match everything via the local filesystem prefix (which
        also must not leak)."""
        path_text = (filters.get("path") or "").strip()
        if path_text:
            escaped = path_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            relative_expr = func.substr(MediaItem.full_file_path, len(str(target).rstrip("/\\")) + 2)
            files_query = files_query.filter(relative_expr.like(f"%{escaped}%", escape="\\"))
        return apply_media_filters(
            session,
            files_query,
            {
                "media_type": filters.get("media_type"),
                "year": filters.get("year"),
                "month": filters.get("month"),
                "device": (filters.get("device") or "").strip() or None,
                "favorite": filters.get("favorite"),
                "person_ids": filters.get("people"),
                "person_match_type": filters.get("person_match_type", "any"),
                "gender": filters.get("gender"),
                "label_ids": filters.get("labels"),
                "labels_match_type": filters.get("labels_match_type", "any"),
                "tag_name": filters.get("tag_name"),
                "tag_value": filters.get("tag_value"),
                "location_names": filters.get("locations"),
                "location_match_type": filters.get("location_match_type", "any"),
                "unnamed": filters.get("unnamed"),
                "proximity_lat": filters.get("proximity_lat"),
                "proximity_lon": filters.get("proximity_lon"),
                "proximity_km": filters.get("proximity_km"),
            },
        )

    def _list_files_facets(self, session, scope_query, filters: dict) -> dict:
        """The filter options that exist within the granted scope — what the
        peer's filter panel renders from. People/labels ship as (id, name)
        pairs whose ids only mean something back on this device; everything
        else is plain values. Only scope-constrained queries here: nothing
        outside the grant may leak into an option list."""
        scope_ids = scope_query.with_entities(MediaItem.id)
        facets = {
            "years": [
                row[0]
                for row in scope_query.with_entities(MediaItem.year)
                .filter(MediaItem.year.isnot(None))
                .distinct()
                .order_by(MediaItem.year)
            ],
            "devices": [
                row[0]
                for row in scope_query.with_entities(MediaItem.device)
                .filter(MediaItem.device.isnot(None), MediaItem.device != "")
                .distinct()
                .order_by(MediaItem.device)
            ],
            "people": [
                {"id": person_id, "name": name}
                for person_id, name in session.query(Person.id, Person.name)
                .join(PersonFace, PersonFace.person_id == Person.id)
                .join(Face, Face.id == PersonFace.face_id)
                .filter(Face.media_item_id.in_(scope_ids), Person.name.isnot(None))
                .distinct()
                .order_by(Person.name)
            ],
            "labels": [
                {"id": label_id, "name": name}
                for label_id, name in session.query(ClassificationLabel.id, ClassificationLabel.name)
                .join(MediaLabel, MediaLabel.label_id == ClassificationLabel.id)
                .filter(MediaLabel.media_item_id.in_(scope_ids))
                .distinct()
                .order_by(func.lower(ClassificationLabel.name))
            ],
            "tag_names": [
                row[0]
                for row in session.query(Tag.tag_name)
                .filter(Tag.media_item_id.in_(scope_ids), Tag.tag_name.isnot(None))
                .distinct()
                .order_by(Tag.tag_name)
            ],
            "locations": [
                row[0]
                for row in scope_query.with_entities(MediaItem.location_name)
                .filter(MediaItem.location_name.isnot(None), MediaItem.location_name != "")
                .distinct()
                .order_by(MediaItem.location_name)
            ],
        }
        if filters.get("tag_name"):
            # Values for the selected tag name, so the peer's tag-value
            # select can populate without a separate protocol message.
            facets["tag_values"] = [
                row[0]
                for row in session.query(Tag.tag_value)
                .filter(
                    Tag.media_item_id.in_(scope_ids),
                    Tag.tag_name == filters["tag_name"],
                    Tag.tag_value.isnot(None),
                )
                .distinct()
                .order_by(Tag.tag_value)
            ]
        return facets

    def _handle_pull_preview(self, body: dict) -> dict:
        """A downscaled JPEG preview of one granted file, compressed here on
        the serving side so the peer's gallery never pulls originals. Videos
        serve their indexed poster frame."""
        with self._session() as session:
            denial = verify_pull_preview_request(body, self._peer_lookup(session))
            if denial is not None:
                return {"status": "error", "detail": denial}
            error, media_dir_id, relative_path, root, target = self._resolve_scoped_request(session, body)
            if error is not None:
                return error
            if not relative_path:
                return {"status": "error", "detail": "relative_path is required"}
            try:
                max_dimension = self._parse_non_negative_int(body.get("max_dimension"), "max_dimension")
            except ValueError as exc:
                return {"status": "error", "detail": str(exc)}
            max_dimension = min(max_dimension or DEFAULT_PREVIEW_DIMENSION, MAX_PREVIEW_DIMENSION)

            item = session.query(MediaItem).filter_by(full_file_path=str(target)).first()
            if item is None:
                return {"status": "error", "detail": "file is not indexed"}
            source = target
            if item.media_type == MEDIA_TYPE_VIDEO:
                if not item.poster_path:
                    return {"status": "error", "detail": "video has no preview"}
                source = Path(item.poster_path).expanduser().resolve()
            if not source.is_file():
                return {"status": "error", "detail": "file is not available"}
            try:
                data = preview_jpeg_bytes(source, max_dimension)
            except Exception:  # noqa: BLE001 — unreadable/corrupt image, codec gaps
                logger.exception("preview generation failed for %s", target)
                return {"status": "error", "detail": "could not generate a preview"}

        return {
            "status": "ok",
            "type": "file_preview",
            "device_id": self.identity.device_id,
            "media_dir_id": media_dir_id,
            "relative_path": relative_path,
            "max_dimension": max_dimension,
            "data_b64": base64.b64encode(data).decode("ascii"),
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

    def _indexed_media_query(self, session, path: Path):
        path_text = str(path).rstrip("/\\")
        under = f"{path_text}{os.sep}%"
        return session.query(MediaItem).filter(
            or_(MediaItem.full_file_path == path_text, MediaItem.full_file_path.like(under))
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
            "date_taken": item.date_taken,
            "location_name": item.location_name,
            "duration_seconds": item.duration_seconds,
        }

    def _grant_allows(self, session, peer_device_id: str, media_dir_id: str, relative_path: str) -> bool:
        """Whether an active grant covers this path — a file being pulled or
        a folder scope being browsed ("" means the media dir root, which only
        a media_dir grant covers)."""
        for grant in p2p_repository.list_active_grants(session, peer_device_id):
            if grant.media_dir_id != media_dir_id:
                continue
            if grant.scope_type == GRANT_SCOPE_MEDIA_DIR:
                return True
            if grant.scope_type == GRANT_SCOPE_FOLDER and grant.relative_path and relative_path:
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

    def _safe_path_component(self, raw_value: str, fallback: str) -> str:
        value = (raw_value or "").strip() or fallback
        cleaned = "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in value).strip()
        if cleaned in ("", ".", ".."):
            return fallback
        return cleaned

    def _clean_destination_path(self, raw_path: str) -> str:
        path = PurePosixPath((raw_path or "").strip())
        if path.is_absolute():
            raise ValueError("destination collection must be relative")
        parts = [
            self._safe_path_component(part, "Files")
            for part in path.parts
            if part not in ("", ".", "..")
        ]
        return PurePosixPath(*parts).as_posix() if parts else "Files"

    def _relative_path_inside_scope(self, relative_path: str, scope_path: Optional[str]) -> str:
        if not scope_path:
            return relative_path
        scope = self._clean_relative_path(scope_path).rstrip("/")
        if relative_path == scope:
            return PurePosixPath(relative_path).name
        prefix = f"{scope}/"
        if relative_path.startswith(prefix):
            return relative_path[len(prefix):]
        return PurePosixPath(relative_path).name

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
