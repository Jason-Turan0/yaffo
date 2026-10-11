"""Pulling one file from a peer: the session it rides on, chunking, resume,
checksums, and restart-on-change.

Two callers share this: TransferManager (the remote gallery's batches) and the
internal P2P API (peer syncs, docs/development/peer-sync.md). Neither batches,
destination layout, nor the UI live here: a caller picks the destination and
watches the entry's state.

Per-file resume state lives in a `{name}.partial.json` sidecar next to the
`.partial` file (scratch state that must not outlive the partial it describes).
The sidecar records the caller's source coordinates plus the size/mtime from the
browse manifest, so a resume can tell "same file, continue appending" from
"source changed, restart from zero" — appending blindly would silently splice
two versions of the file.

Everything here runs on the P2P engine's asyncio loop.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from typing import Awaitable, Callable, Optional, Protocol

from yaffo.logging_config import get_logger
from yaffo.p2p.handlers.pull_file import build_pull_file_request
from yaffo.p2p.handlers.sharing import DEFAULT_PULL_CHUNK_BYTES
from yaffo.p2p.quic_transport import TransportError
from yaffo.p2p.rate_limits import RATE_LIMITED_CODE
from yaffo.p2p.signaling import CallError

logger = get_logger(__name__, "webapp")

# Chunk requests carry ~1 MiB of base64 payload; allow for slow relayed
# paths without letting a dead peer stall a worker forever.
TRANSFER_REQUEST_TIMEOUT_SECONDS = 60.0
# One transport failure means reconnect-and-resume; this many consecutive
# failed reopens means the peer is gone.
MAX_SESSION_REOPENS = 3
# One restart-from-zero per file (source changed mid-pull, or the final
# checksum failed); a second means something is actively wrong.
MAX_FILE_RESTARTS = 1
# Relayed bytes are paced to this rate (the relay is a shared e2-micro that also
# carries everyone's signaling). Direct and LAN paths are never paced.
RELAY_THROTTLE_BYTES_PER_SECOND = 4 * 1024 * 1024
# A peer that answers rate_limited is asked again after its retry_after (capped),
# this many times in a row before the response is handed back as a failure.
MAX_RATE_LIMITED_RETRIES = 5
MAX_RATE_LIMIT_WAIT_SECONDS = 5.0

FILE_QUEUED = "queued"
FILE_ACTIVE = "active"
FILE_DONE = "done"
FILE_SKIPPED = "skipped"
FILE_FAILED = "failed"
FILE_CANCELLED = "cancelled"

# Why a file failed, for callers that map failures onto their own outcomes.
PROBLEM_REFUSED = "refused"            # the peer said no (left the grant, unknown id)
PROBLEM_CHANGED = "changed"            # the source kept changing during the pull
PROBLEM_CHECKSUM = "checksum"          # the whole-file hash never matched
PROBLEM_BAD_CHUNK = "bad_chunk"        # a malformed chunk (wrong file, offset, size, hash)


class TransferAborted(Exception):
    """The pull cannot continue (session gone for good, or cancelled)."""


class PeerSession:
    """Unified handle for one open session to a peer, whatever the path.

    `inner` is a HubSession or a PinnedConnection — anything with an async
    request(payload, timeout), a close(), and a `closed` property. request()
    returns the peer's response verbatim (error statuses included); only
    transport failures raise.
    """

    def __init__(self, path: str, inner) -> None:
        self.path = path
        self._inner = inner

    @property
    def closed(self) -> bool:
        return bool(getattr(self._inner, "closed", False))

    async def request(self, payload: dict, timeout: float = TRANSFER_REQUEST_TIMEOUT_SECONDS) -> dict:
        return await self._inner.request(payload, timeout)

    def close(self) -> None:
        self._inner.close()


class SessionHolder:
    """One shared session to a peer, with reconnect-on-failure.

    All pulls issue requests through the holder; a transport failure
    invalidates the shared session and the next request reopens it (single
    flight — the lock serializes reopens; the identity check stops two
    workers from double-closing). A file interrupted by the relay's 8 GiB
    per-session byte cap, an idle reap, or a network blip resumes on the
    fresh session from its verified offset.
    """

    def __init__(self, opener: Callable[[], Awaitable[PeerSession]]) -> None:
        self._opener = opener
        self._session: Optional[PeerSession] = None
        self._lock = asyncio.Lock()
        self._consecutive_failures = 0

    @property
    def path(self) -> Optional[str]:
        return self._session.path if self._session is not None else None

    async def open(self) -> str:
        """Open the session now (instead of on the first request) and return its path."""
        return (await self._acquire()).path

    async def _acquire(self) -> PeerSession:
        async with self._lock:
            if self._session is None or self._session.closed:
                if self._consecutive_failures >= MAX_SESSION_REOPENS:
                    raise TransferAborted("giving up after repeated session failures")
                self._session = await self._opener()
                logger.info("transfer session open path=%s", self._session.path)
            return self._session

    async def _invalidate(self, session: PeerSession) -> None:
        async with self._lock:
            if self._session is session:
                session.close()
                self._session = None

    async def request(self, payload: dict, timeout: float = TRANSFER_REQUEST_TIMEOUT_SECONDS) -> dict:
        """Send one request on the shared session. A rate_limited answer is the
        peer asking us to slow down, not a failure: wait and ask again."""
        for _ in range(MAX_RATE_LIMITED_RETRIES):
            response = await self._request_once(payload, timeout)
            if response.get("code") != RATE_LIMITED_CODE:
                return response
            try:
                wait = float(response.get("retry_after") or 1.0)
            except (TypeError, ValueError):
                wait = 1.0
            await asyncio.sleep(min(max(wait, 0.05), MAX_RATE_LIMIT_WAIT_SECONDS))
        return response

    async def _request_once(self, payload: dict, timeout: float) -> dict:
        last_error: Optional[Exception] = None
        for _ in range(2):
            try:
                session = await self._acquire()
            except (CallError, TransportError) as exc:
                # Reopening failed — the peer is unreachable, not just one
                # request unlucky.
                self._consecutive_failures += 1
                raise TransferAborted(f"could not reach the peer: {exc}") from exc
            try:
                response = await session.request(payload, timeout)
                self._consecutive_failures = 0
                return response
            except TransportError as exc:
                last_error = exc
                self._consecutive_failures += 1
                await self._invalidate(session)
        raise TransferAborted(f"transfer request kept failing: {last_error}") from last_error

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None


class RelayMeter(Protocol):
    """Whatever relayed bytes are counted against: a batch, or an API session."""

    relay_bytes: int
    relay_pace_started: Optional[float]


async def pace_relay_bytes(meter: RelayMeter, count: int) -> None:
    """Count relayed payload bytes and sleep just enough to keep `meter` at or
    under RELAY_THROTTLE_BYTES_PER_SECOND."""
    loop_time = asyncio.get_running_loop().time()
    if meter.relay_pace_started is None:
        meter.relay_pace_started = loop_time
    meter.relay_bytes += count
    expected_elapsed = meter.relay_bytes / RELAY_THROTTLE_BYTES_PER_SECOND
    actual_elapsed = loop_time - meter.relay_pace_started
    if expected_elapsed > actual_elapsed:
        await asyncio.sleep(min(expected_elapsed - actual_elapsed, 5.0))


class PullEntry(Protocol):
    """What a pull reads and reports through: the peer's media item id (the only
    thing a pull request names) and the manifest's size/mtime going in; state,
    progress, and the outcome coming out."""

    media_item_id: int
    size: int
    mtime: float
    state: str
    bytes_done: int
    error: Optional[str]
    problem: Optional[str]
    sha256: Optional[str]


ChunkHook = Callable[[SessionHolder], Awaitable[None]]
BytesHook = Callable[[SessionHolder, int], Awaitable[None]]


async def _no_wait(_holder: SessionHolder) -> None:
    return None


async def _no_count(_holder: SessionHolder, _count: int) -> None:
    return None


class PeerPuller:
    """Pulls single files over a SessionHolder.

    `before_chunk` runs before every chunk request (the relay budget waits
    there), `after_chunk` after every chunk written (relay accounting and
    pacing), and `is_cancelled` is polled between chunks; a cancelled pull
    raises TransferAborted and keeps its partial for a later resume.
    """

    def __init__(
        self,
        identity,
        holder: SessionHolder,
        *,
        before_chunk: ChunkHook = _no_wait,
        after_chunk: BytesHook = _no_count,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> None:
        self._identity = identity
        self._holder = holder
        self._before_chunk = before_chunk
        self._after_chunk = after_chunk
        self._is_cancelled = is_cancelled

    async def pull(self, entry: PullEntry, destination: Path, source: dict) -> None:
        """Pull `entry` to `destination` (its parent must exist), resuming a
        matching partial. `source` names the file stably for the resume sidecar.
        Sets entry.state to done or failed; raises TransferAborted when the
        session is gone for good or the pull was cancelled."""
        partial = destination.with_name(f"{destination.name}.partial")
        sidecar = destination.with_name(f"{destination.name}.partial.json")
        entry.state = FILE_ACTIVE

        restarts = 0
        while True:
            outcome, problem, detail = await self._pull_once(entry, source, partial, sidecar)
            if outcome == "done":
                partial.replace(destination)
                sidecar.unlink(missing_ok=True)
                entry.state = FILE_DONE
                entry.error = None
                entry.problem = None
                return
            if outcome == "failed":
                entry.state = FILE_FAILED
                entry.error = detail
                entry.problem = problem
                return
            # outcome == "restart": the source changed under us or the final
            # checksum failed. Either way the partial is untrustworthy — a
            # stale partial must never wedge every future retry.
            partial.unlink(missing_ok=True)
            sidecar.unlink(missing_ok=True)
            entry.bytes_done = 0
            restarts += 1
            if restarts > MAX_FILE_RESTARTS:
                entry.state = FILE_FAILED
                entry.error = detail
                entry.problem = problem
                return

    @staticmethod
    def _expectation(entry: PullEntry, source: dict) -> dict:
        return {**source, "size": entry.size, "mtime": entry.mtime}

    def _prepare_resume(
        self, entry: PullEntry, source: dict, partial: Path, sidecar: Path
    ) -> tuple[int, "hashlib._Hash"]:
        """Decide the starting offset: resume the partial only when its
        sidecar matches the manifest we're pulling against (same source,
        same size/mtime); anything else restarts from zero. Returns the
        offset and a running sha256 primed with the resumed bytes, so the
        end-to-end check covers them too."""
        expectation = self._expectation(entry, source)
        if partial.exists() and sidecar.exists():
            try:
                recorded = json.loads(sidecar.read_text())
            except (ValueError, OSError):
                recorded = None
            if recorded == expectation:
                hasher = hashlib.sha256()
                with partial.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(block)
                return partial.stat().st_size, hasher
        partial.unlink(missing_ok=True)
        sidecar.write_text(json.dumps(expectation))
        partial.touch()
        return 0, hashlib.sha256()

    async def _pull_once(
        self, entry: PullEntry, source: dict, partial: Path, sidecar: Path
    ) -> tuple[str, Optional[str], Optional[str]]:
        """One attempt at pulling the file to completion. Returns
        (outcome, problem, detail) with outcome "done", "failed" or "restart"."""
        offset, hasher = self._prepare_resume(entry, source, partial, sidecar)
        entry.bytes_done = offset
        with partial.open("ab") as handle:
            while True:
                if self._is_cancelled():
                    raise TransferAborted("cancelled")
                await self._before_chunk(self._holder)
                payload = build_pull_file_request(
                    self._identity, entry.media_item_id, offset, DEFAULT_PULL_CHUNK_BYTES
                )
                chunk = await self._holder.request(payload)
                if chunk.get("status") != "ok":
                    return "failed", PROBLEM_REFUSED, chunk.get("detail") or "peer refused the file"

                problem = self._chunk_problem(chunk, entry.media_item_id, offset)
                if problem is not None:
                    return "failed", PROBLEM_BAD_CHUNK, problem
                if entry.size and (chunk.get("size") != entry.size or self._mtime_changed(chunk, entry)):
                    # The source file changed since the manifest snapshot —
                    # appending would splice two versions. Adopt the new
                    # identity and restart.
                    entry.size = int(chunk.get("size") or 0)
                    entry.mtime = float(chunk.get("mtime") or 0)
                    return "restart", PROBLEM_CHANGED, "the source file changed while it was being pulled"
                if not entry.size:
                    entry.size = int(chunk.get("size") or 0)
                    entry.mtime = float(chunk.get("mtime") or 0)
                    sidecar.write_text(json.dumps(self._expectation(entry, source)))

                data = base64.b64decode(chunk["data_b64"])
                handle.write(data)
                handle.flush()
                hasher.update(data)
                offset = chunk["next_offset"]
                entry.bytes_done = offset
                await self._after_chunk(self._holder, len(data))

                if chunk.get("eof"):
                    digest = hasher.hexdigest()
                    expected_hash = chunk.get("file_sha256")
                    if expected_hash and digest != expected_hash:
                        return "restart", PROBLEM_CHECKSUM, "the downloaded file did not match the peer's checksum"
                    entry.sha256 = digest
                    return "done", None, None

    @staticmethod
    def _mtime_changed(chunk: dict, entry: PullEntry) -> bool:
        mtime = chunk.get("mtime")
        if mtime is None or not entry.mtime:
            return False  # older peers don't send mtime; size + final checksum still protect us
        return abs(float(mtime) - entry.mtime) > 1e-6

    @staticmethod
    def _chunk_problem(chunk: dict, media_item_id: int, offset: int) -> Optional[str]:
        # The chunk must be for the item we asked for. (A peer that re-indexed since the
        # manifest was taken can hand back a different file for a recycled id — the
        # size/mtime and final checksum checks are what catch that.)
        if chunk.get("media_item_id") != media_item_id:
            return "peer returned a chunk for a different file"
        if chunk.get("offset") != offset:
            return "peer returned a chunk at the wrong offset"
        data = base64.b64decode(chunk.get("data_b64") or "")
        if len(data) != chunk.get("bytes"):
            return "peer returned a chunk with the wrong byte count"
        if hashlib.sha256(data).hexdigest() != chunk.get("chunk_sha256"):
            return "peer returned a chunk with the wrong checksum"
        if not data and not chunk.get("eof"):
            return "peer returned an empty chunk before the end of the file"
        return None
