"""The engine side of the internal P2P API (docs/development/peer-sync.md):
sessions and pulls that local processes (sync workers) drive over
`/internal/p2p/v1`, run on this web process's one P2P engine.

A session is one pinned connection to one peer, shared by every owner that
asks for that peer (reference-counted), closed when its last owner lets go or
after IDLE_EXPIRY_SECONDS with nothing to do. A pull downloads one file over a
session to a destination the route already validated; the web process writes
the bytes, the caller only polls.

Sessions and pulls live in memory on the engine loop. A web restart loses them,
which is fine: the caller sees `session_expired` (or a refused connection) and
the next run resumes from the partials on disk.

Everything is mutated on the engine loop only; the synchronous facade methods
bridge from web threads through the service's _submit.
"""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from yaffo.logging_config import get_logger
from yaffo.p2p.errors import P2PServiceError
from yaffo.p2p.handlers.list_files import build_list_files_request
from yaffo.p2p.handlers.sharing import MAX_LIST_FILES_LIMIT
from yaffo.p2p.puller import (
    FILE_ACTIVE,
    FILE_CANCELLED,
    FILE_DONE,
    FILE_FAILED,
    FILE_QUEUED,
    PROBLEM_BAD_CHUNK,
    PROBLEM_CHANGED,
    PROBLEM_CHECKSUM,
    PROBLEM_REFUSED,
    PeerPuller,
    SessionHolder,
    TransferAborted,
    pace_relay_bytes,
)
from yaffo.p2p.quic_transport import TransportError
from yaffo.p2p.signaling import CallError

logger = get_logger(__name__, "webapp")

API_VERSION = 1
IDLE_EXPIRY_SECONDS = 120.0
REAP_INTERVAL_SECONDS = 15.0
# Pulls that run at once on one session — streams on its one connection, as
# TransferManager does per batch. More are queued.
SESSION_PULL_CONCURRENCY = 3
# Finished pulls stay readable this long, then are forgotten.
FINISHED_PULL_TTL_SECONDS = 600.0
MAX_WAIT_SECONDS = 10.0
FACADE_TIMEOUT_SECONDS = 10.0

RELAY_NEVER = "never"
RELAY_BUDGET = "budget"
RELAY_POLICIES = (RELAY_NEVER, RELAY_BUDGET)

# What the API calls each transport path (PeerSession says "local" for LAN).
API_PATHS = {"local": "lan", "direct": "direct", "relay": "relay"}

# Error codes and their HTTP statuses. Stable: the peer source maps them onto
# sync engine outcomes.
ENGINE_UNAVAILABLE = "engine_unavailable"
PEER_OFFLINE = "peer_offline"
RELAY_NOT_ALLOWED = "relay_not_allowed"
RELAY_BUDGET_REACHED = "relay_budget_reached"
NOT_TRUSTED = "not_trusted"
SCOPE_NOT_SHARED = "scope_not_shared"
FILE_REFUSED = "file_refused"
SOURCE_CHANGED = "source_changed"
CHECKSUM_MISMATCH = "checksum_mismatch"
BAD_DESTINATION = "bad_destination"
SESSION_EXPIRED = "session_expired"
INVALID_REQUEST = "invalid_request"
FORBIDDEN = "internal_only"
INTERNAL_ERROR = "internal_error"

ERROR_STATUS = {
    ENGINE_UNAVAILABLE: 503,
    PEER_OFFLINE: 409,
    RELAY_NOT_ALLOWED: 409,
    RELAY_BUDGET_REACHED: 409,
    NOT_TRUSTED: 403,
    SCOPE_NOT_SHARED: 404,
    FILE_REFUSED: 404,
    SOURCE_CHANGED: 409,
    CHECKSUM_MISMATCH: 502,
    BAD_DESTINATION: 400,
    SESSION_EXPIRED: 404,
    INVALID_REQUEST: 400,
    FORBIDDEN: 403,
    INTERNAL_ERROR: 500,
}

PROBLEM_CODES = {
    PROBLEM_REFUSED: FILE_REFUSED,
    PROBLEM_CHANGED: SOURCE_CHANGED,
    PROBLEM_CHECKSUM: CHECKSUM_MISMATCH,
    PROBLEM_BAD_CHUNK: CHECKSUM_MISMATCH,
}

# The serving side's denial when it no longer trusts us (messages.verify_signed_message).
_UNTRUSTED_DETAIL = "not a trusted device"


class PeerApiError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status(self) -> int:
        return ERROR_STATUS.get(self.code, 500)


class RelayNotAllowed(TransferAborted):
    """The session only got a relayed path and its policy forbids relay."""


class RelayBudgetReached(TransferAborted):
    """The session pushed its relay budget through the relay."""


def peer_denial_code(detail: str, default: str) -> str:
    return NOT_TRUSTED if _UNTRUSTED_DETAIL in (detail or "") else default


# ---- scopes ------------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    media_dir_id: str = ""
    relative_path: str = ""
    album_id: Optional[int] = None


def scope_key(scope: dict) -> str:
    """The string a sync stores as its option id. Deterministic, so it stays
    valid for as long as the grant exists."""
    if scope.get("scope_type") == "album":
        return f"album:{scope['album_id']}"
    if scope.get("relative_path"):
        return f"folder:{scope['media_dir_id']}:{scope['relative_path']}"
    return f"media_dir:{scope['media_dir_id']}"


def parse_scope_key(key: str) -> Scope:
    kind, _, rest = (key or "").partition(":")
    if kind == "album":
        try:
            return Scope(album_id=int(rest))
        except ValueError:
            pass
    elif kind == "media_dir" and rest:
        return Scope(media_dir_id=rest)
    elif kind == "folder":
        media_dir_id, _, relative_path = rest.partition(":")
        if media_dir_id and relative_path:
            return Scope(media_dir_id=media_dir_id, relative_path=relative_path)
    raise PeerApiError(INVALID_REQUEST, f"not a scope key: {key!r}")


def source_id(manifest: dict) -> str:
    """The ledger key: stable across the peer re-indexing (media item ids aren't)."""
    return f"{manifest.get('media_dir_id')}:{manifest.get('relative_path')}"


def files_page(response: dict, cursor: int) -> dict:
    files = [{**manifest, "source_id": source_id(manifest)} for manifest in response.get("files") or []]
    total = int(response.get("total") or 0)
    # A listed database row may no longer have a file on disk. The serving side
    # omits its manifest, but its page still consumed that row's offset.
    page_limit = int(response.get("limit") or len(files))
    next_offset = cursor + page_limit
    return {
        "files": files,
        "next_cursor": str(next_offset) if page_limit and next_offset < total else None,
        "total": total,
    }


# ---- sessions and pulls --------------------------------------------------------


@dataclass
class ApiPull:
    id: str
    session_id: str
    media_item_id: int
    source_id: str
    size: int
    mtime: float
    destination: Path
    state: str = FILE_QUEUED
    bytes_done: int = 0
    error: Optional[str] = None
    problem: Optional[str] = None
    sha256: Optional[str] = None
    code: Optional[str] = None
    cancelled: bool = False
    finished_at: Optional[float] = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None

    @property
    def finished(self) -> bool:
        return self.state in (FILE_DONE, FILE_FAILED, FILE_CANCELLED)

    def describe(self) -> dict:
        state = self.state
        error = None
        if state == FILE_FAILED:
            error = {"code": self.code or INTERNAL_ERROR, "message": self.error or ""}
        return {
            "pull_id": self.id,
            "state": state,
            "bytes_done": self.bytes_done,
            "size": self.size,
            "sha256": self.sha256 if state == FILE_DONE else None,
            "error": error,
        }


@dataclass
class ApiSession:
    id: str
    peer_device_id: str
    relay: str
    relay_budget_bytes: int
    owners: set = field(default_factory=set)
    holder: Optional[SessionHolder] = None
    relay_bytes: int = 0
    relay_pace_started: Optional[float] = None
    last_used: float = 0.0
    closed: bool = False
    pulls: dict = field(default_factory=dict)
    slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(SESSION_PULL_CONCURRENCY))

    @property
    def path(self) -> Optional[str]:
        raw = self.holder.path if self.holder is not None else None
        return API_PATHS.get(raw, raw) if raw else None

    @property
    def active_pulls(self) -> int:
        return sum(1 for pull in self.pulls.values() if not pull.finished)

    def describe(self) -> dict:
        return {
            "session_id": self.id,
            "device_id": self.peer_device_id,
            "path": self.path,
            "relay": self.relay,
            "relay_bytes": self.relay_bytes,
            "pulls_active": self.active_pulls,
            "idle_expires_in": IDLE_EXPIRY_SECONDS,
        }


class PeerApi:
    """Sessions and pulls for the internal API. Owned by P2PService."""

    def __init__(self, service) -> None:
        self._service = service
        self._sessions: dict[str, ApiSession] = {}
        self._pulls: dict[str, ApiPull] = {}
        self._reaper: Optional[asyncio.Task] = None

    # ---- synchronous facade (web threads) ----------------------------------

    def open_session(self, peer_device_id: str, relay: str, relay_budget_bytes: int, owner: str) -> dict:
        return self._run(self._open_session(peer_device_id, relay, relay_budget_bytes, owner),
                         timeout=FACADE_TIMEOUT_SECONDS + 50.0)

    def session_info(self, session_id: str) -> dict:
        return self._run(self._session_info(session_id))

    def close_session(self, session_id: str, owner: Optional[str]) -> None:
        self._run(self._close_session(session_id, owner))

    def list_files(self, session_id: str, scope: Scope, cursor: int, limit: int,
                   filters: Optional[dict] = None) -> dict:
        return self._run(self._list_files(session_id, scope, cursor, limit, filters or {}),
                         timeout=FACADE_TIMEOUT_SECONDS + 50.0)

    def start_pull(self, session_id: str, media_item_id: int, source: str, size: int, mtime: float,
                   destination: Path) -> dict:
        return self._run(self._start_pull(session_id, media_item_id, source, size, mtime, destination))

    def pull_status(self, pull_id: str, wait: float) -> dict:
        wait = max(0.0, min(wait, MAX_WAIT_SECONDS))
        return self._run(self._pull_status(pull_id, wait), timeout=wait + FACADE_TIMEOUT_SECONDS)

    def cancel_pull(self, pull_id: str) -> None:
        self._run(self._cancel_pull(pull_id))

    def _run(self, coro, timeout: float = FACADE_TIMEOUT_SECONDS):
        try:
            return self._service._submit(coro, timeout)
        except P2PServiceError as exc:
            raise PeerApiError(ENGINE_UNAVAILABLE, str(exc)) from exc

    # ---- engine loop ---------------------------------------------------------

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.ensure_future(self._reap_forever())

    async def _reap_forever(self) -> None:
        while self._sessions or self._pulls:
            await asyncio.sleep(REAP_INTERVAL_SECONDS)
            self._reap()

    def _reap(self) -> None:
        now = self._now()
        for session in list(self._sessions.values()):
            if session.active_pulls == 0 and now - session.last_used > IDLE_EXPIRY_SECONDS:
                logger.info("internal p2p session %s idle-expired", session.id)
                self._shut(session)
        for pull in list(self._pulls.values()):
            if pull.finished and pull.finished_at is not None and now - pull.finished_at > FINISHED_PULL_TTL_SECONDS:
                self._pulls.pop(pull.id, None)

    def _session(self, session_id: str) -> ApiSession:
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            raise PeerApiError(SESSION_EXPIRED, "unknown or expired session")
        session.last_used = self._now()
        return session

    def _session_for_peer(self, peer_device_id: str) -> Optional[ApiSession]:
        return next(
            (s for s in self._sessions.values() if s.peer_device_id == peer_device_id and not s.closed), None
        )

    async def _open_session(self, peer_device_id: str, relay: str, relay_budget_bytes: int, owner: str) -> dict:
        self._ensure_reaper()
        session = self._session_for_peer(peer_device_id)
        if session is not None:
            # One connection per peer. The strictest owner's relay policy applies.
            if relay == RELAY_NEVER and session.path == "relay":
                raise PeerApiError(RELAY_NOT_ALLOWED, "the open session to this device is relayed")
            if relay == RELAY_NEVER:
                session.relay = RELAY_NEVER
            session.relay_budget_bytes = min(session.relay_budget_bytes, relay_budget_bytes)
            session.owners.add(owner)
            session.last_used = self._now()
            return session.describe()

        session = ApiSession(
            id=f"s_{secrets.token_hex(8)}",
            peer_device_id=peer_device_id,
            relay=relay,
            relay_budget_bytes=relay_budget_bytes,
            owners={owner},
            last_used=self._now(),
        )

        async def opener():
            peer_session = await self._service.open_peer_session(peer_device_id)
            if session.relay == RELAY_NEVER and peer_session.path == "relay":
                peer_session.close()
                raise RelayNotAllowed("only a relayed path is available")
            return peer_session

        session.holder = SessionHolder(opener)
        try:
            await session.holder.open()
        except RelayNotAllowed as exc:
            raise PeerApiError(RELAY_NOT_ALLOWED, str(exc)) from exc
        except (CallError, TransportError, TransferAborted) as exc:
            raise PeerApiError(PEER_OFFLINE, str(exc)) from exc
        self._sessions[session.id] = session
        logger.info("internal p2p session %s open peer=%s path=%s relay=%s",
                    session.id, peer_device_id, session.path, relay)
        return session.describe()

    async def _session_info(self, session_id: str) -> dict:
        return self._session(session_id).describe()

    async def _close_session(self, session_id: str, owner: Optional[str]) -> None:
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            return
        if owner is not None:
            session.owners.discard(owner)
            if session.owners:
                return
        self._shut(session)

    def _shut(self, session: ApiSession) -> None:
        session.closed = True
        for pull in session.pulls.values():
            if not pull.finished:
                pull.cancelled = True
                if pull.task is not None:
                    pull.task.cancel()
        if session.holder is not None:
            session.holder.close()
        self._sessions.pop(session.id, None)

    async def _list_files(self, session_id: str, scope: Scope, cursor: int, limit: int,
                          filters: Optional[dict] = None) -> dict:
        session = self._session(session_id)
        payload = build_list_files_request(
            self._service.identity, scope.media_dir_id, scope.relative_path, filters or {}, cursor,
            min(limit, MAX_LIST_FILES_LIMIT), album_id=scope.album_id,
        )
        try:
            response = await session.holder.request(payload)
        except TransferAborted as exc:
            raise PeerApiError(PEER_OFFLINE, str(exc)) from exc
        if response.get("status") != "ok":
            detail = response.get("detail") or "the device refused to list this scope"
            raise PeerApiError(peer_denial_code(detail, SCOPE_NOT_SHARED), detail)
        return files_page(response, cursor)

    async def _start_pull(self, session_id: str, media_item_id: int, source: str, size: int, mtime: float,
                          destination: Path) -> dict:
        session = self._session(session_id)
        # Two writers on one partial would splice it. A pull still running to this
        # destination (started ahead by a run that has since ended, say) is the
        # caller's pull now.
        running = next((p for p in self._pulls.values()
                        if p.destination == destination and not p.finished and not p.cancelled), None)
        if running is not None:
            return {"pull_id": running.id}
        pull = ApiPull(
            id=f"p_{secrets.token_hex(8)}",
            session_id=session.id,
            media_item_id=media_item_id,
            source_id=source,
            size=size,
            mtime=mtime,
            destination=destination,
        )
        self._pulls[pull.id] = pull
        session.pulls[pull.id] = pull
        pull.task = asyncio.ensure_future(self._run_pull(session, pull))
        return {"pull_id": pull.id}

    def _set_state(self, pull: ApiPull, state: str) -> None:
        pull.state = state
        if pull.finished:
            pull.finished_at = self._now()
        changed, pull.changed = pull.changed, asyncio.Event()
        changed.set()

    async def _run_pull(self, session: ApiSession, pull: ApiPull) -> None:
        try:
            async with session.slots:
                if pull.cancelled:
                    raise asyncio.CancelledError
                self._set_state(pull, FILE_ACTIVE)
                puller = PeerPuller(
                    self._service.identity,
                    session.holder,
                    before_chunk=lambda holder: self._respect_relay_budget(session, holder),
                    after_chunk=lambda holder, count: self._account_relay(session, holder, count),
                    is_cancelled=lambda: pull.cancelled or session.closed,
                )
                source = {"peer_device_id": session.peer_device_id, "source_id": pull.source_id}
                await puller.pull(pull, pull.destination, source)
                if pull.state == FILE_FAILED:
                    pull.code = peer_denial_code(pull.error, PROBLEM_CODES.get(pull.problem, INTERNAL_ERROR))
                session.last_used = self._now()
                self._set_state(pull, pull.state)
        except asyncio.CancelledError:
            self._set_state(pull, FILE_CANCELLED)
        except TransferAborted as exc:
            if pull.cancelled or session.closed:
                self._set_state(pull, FILE_CANCELLED)
                return
            pull.code = (RELAY_NOT_ALLOWED if isinstance(exc, RelayNotAllowed)
                         else RELAY_BUDGET_REACHED if isinstance(exc, RelayBudgetReached)
                         else PEER_OFFLINE)
            pull.error = str(exc)
            self._set_state(pull, FILE_FAILED)
        except Exception as exc:  # noqa: BLE001 — a pull must never take the engine loop down
            logger.exception("internal p2p pull %s crashed", pull.id)
            pull.code = INTERNAL_ERROR
            pull.error = str(exc)
            self._set_state(pull, FILE_FAILED)

    async def _respect_relay_budget(self, session: ApiSession, holder: SessionHolder) -> None:
        """Unattended runs can't "continue anyway": at the budget, pulls stop."""
        if holder.path == "relay" and session.relay_bytes >= session.relay_budget_bytes:
            raise RelayBudgetReached("the session used its relay budget")

    async def _account_relay(self, session: ApiSession, holder: SessionHolder, count: int) -> None:
        session.last_used = self._now()
        if holder.path == "relay":
            await pace_relay_bytes(session, count)

    async def _pull_status(self, pull_id: str, wait: float) -> dict:
        pull = self._pulls.get(pull_id)
        if pull is None:
            raise PeerApiError(SESSION_EXPIRED, "unknown or expired pull")
        session = self._sessions.get(pull.session_id)
        if session is not None:
            session.last_used = self._now()
        if wait > 0 and not pull.finished:
            try:
                await asyncio.wait_for(pull.changed.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
        return pull.describe()

    async def _cancel_pull(self, pull_id: str) -> None:
        pull = self._pulls.get(pull_id)
        if pull is None or pull.finished:
            return
        pull.cancelled = True
        if pull.task is not None:
            pull.task.cancel()
