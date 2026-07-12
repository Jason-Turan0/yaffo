"""Phase 6 batch transfers: manifest-driven background pulls over one
transfer session per (peer, batch).

The unit of connection setup is the SESSION, not the chunk: a batch opens a
single pinned connection (LAN candidate first, else the hub's relay-first
flow with a one-time punch upgrade) and every chunk of every file rides it
as one QUIC stream. When the upgrade lands, all bulk bytes move on the free
direct path; when it doesn't, the session stays on the relay — throttled and
subject to a soft per-batch budget, because relay egress is the hub's only
metered cost.

Everything here runs on the P2P engine's asyncio loop (NOT the taskq — a
second process asserting the same device identity would fight this process's
hub WebSocket). Flask routes talk to TransferManager through its synchronous
facade methods, which bridge with run_coroutine_threadsafe.

Per-file resume state lives in a `{name}.partial.json` sidecar next to the
`.partial` file (per the design doc: scratch state that must not outlive the
partial it describes). The sidecar records the source coordinates and the
size/mtime from the browse manifest, so a resume can tell "same file,
continue appending" from "source changed, restart from zero" — appending
blindly would silently splice two versions of the file.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from yaffo.logging_config import get_logger
from yaffo.p2p.handlers.list_files import build_list_files_request
from yaffo.p2p.handlers.pull_file import build_pull_file_request
from yaffo.p2p.handlers.sharing import (
    DEFAULT_PULL_CHUNK_BYTES,
    MAX_LIST_FILES_LIMIT,
    clean_destination_path,
    clean_relative_path,
    path_inside,
    relative_path_inside_scope,
    safe_path_component,
)
from yaffo.p2p.quic_transport import TransportError
from yaffo.p2p.signaling import CallError

logger = get_logger(__name__, "webapp")

# Chunk requests carry ~1 MiB of base64 payload; allow for slow relayed
# paths without letting a dead peer stall a worker forever.
TRANSFER_REQUEST_TIMEOUT_SECONDS = 60.0
# Concurrent files per batch — as parallel streams on the ONE session
# connection (which punches once and shares congestion control), never as
# parallel connections.
TRANSFER_FILE_CONCURRENCY = 3
# One transport failure means reconnect-and-resume; this many consecutive
# failed reopens means the peer is gone and the batch fails.
MAX_SESSION_REOPENS = 3
# One restart-from-zero per file (source changed mid-pull, or the final
# checksum failed); a second means something is actively wrong.
MAX_FILE_RESTARTS = 1
# Relay policy: discourage, don't forbid. Relayed sessions are paced (the
# relay is a shared e2-micro that also carries everyone's signaling) and a
# batch that pushes more than the budget through the relay pauses until the
# user explicitly continues.
RELAY_THROTTLE_BYTES_PER_SECOND = 4 * 1024 * 1024
RELAY_BATCH_BUDGET_BYTES = 1 * 2**30
# Finished batches kept for the status UI.
MAX_FINISHED_BATCHES_KEPT = 20

STATE_COLLECTING = "collecting"
STATE_RUNNING = "running"
STATE_PAUSED_RELAY_BUDGET = "paused_relay_budget"
STATE_COMPLETED = "completed"
STATE_COMPLETED_WITH_ERRORS = "completed_with_errors"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
ACTIVE_STATES = (STATE_COLLECTING, STATE_RUNNING, STATE_PAUSED_RELAY_BUDGET)

FILE_QUEUED = "queued"
FILE_ACTIVE = "active"
FILE_DONE = "done"
FILE_SKIPPED = "skipped"
FILE_FAILED = "failed"
FILE_CANCELLED = "cancelled"


class TransferAborted(Exception):
    """The batch cannot continue (session gone for good, or cancelled)."""


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


class _SessionHolder:
    """The batch's one shared session, with reconnect-on-failure.

    All file workers issue requests through the holder; a transport failure
    invalidates the shared session and the next request reopens it (single
    flight — the lock serializes reopens; the identity check stops two
    workers from double-closing). A file interrupted by the relay's 8 GiB
    per-session byte cap, an idle reap, or a network blip resumes on the
    fresh session from its verified offset.
    """

    def __init__(self, opener: Callable) -> None:
        self._opener = opener
        self._session: Optional[PeerSession] = None
        self._lock = asyncio.Lock()
        self._consecutive_failures = 0

    @property
    def path(self) -> Optional[str]:
        return self._session.path if self._session is not None else None

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


@dataclass
class TransferFile:
    relative_path: str
    name: str
    size: int
    mtime: float
    state: str = FILE_QUEUED
    bytes_done: int = 0
    error: Optional[str] = None


@dataclass
class TransferBatch:
    id: str
    peer_device_id: str
    peer_name: str
    media_dir_id: str
    scope: str
    label: str
    filters: dict
    destination_root: Path
    collection_path: str
    # A selection over the scope, resolved against the peer's manifest (see
    # _collect_files). `included` empty means "everything matching the filters";
    # `excluded` removes paths from whatever that yields. The browser only ever
    # sends relative paths — sizes and mtimes come from the peer's manifest, which
    # is what the resume sidecars must be seeded from.
    included: set = field(default_factory=set)
    excluded: set = field(default_factory=set)
    files: list = field(default_factory=list)
    state: str = STATE_COLLECTING
    path: Optional[str] = None
    relay_bytes: int = 0
    total_expected: int = 0
    error: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    finished_at: Optional[datetime] = None
    cancelled: bool = False
    relay_overage_allowed: bool = False
    # Set on the engine loop when the batch is admitted.
    resume_event: Optional[asyncio.Event] = None
    relay_pace_started: Optional[float] = None

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES


class TransferManager:
    """Owns every transfer batch in this process. Mutated only on the P2P
    engine loop; Flask routes use the synchronous facades, which bridge via
    the service's _submit."""

    def __init__(self, service) -> None:
        self._service = service
        self._batches: dict[str, TransferBatch] = {}

    # ---- synchronous facades (web threads) --------------------------------

    def start_batch(
        self,
        peer_device_id: str,
        peer_name: str,
        media_dir_id: str,
        scope: str,
        label: str,
        filters: dict,
        destination_root: Path,
        collection_path: str,
        files: Optional[list[dict]] = None,
        include_paths: Optional[list[str]] = None,
        exclude_paths: Optional[list[str]] = None,
    ) -> str:
        """Enqueue a batch and return its id immediately.

        With explicit `files` (manifest dicts) those are pulled. Otherwise the batch
        collects the manifest for the scope+filters from the peer and applies the
        selection to it: `include_paths` keeps only those relative paths, and
        `exclude_paths` drops them ("everything matching, except these"). Resolving
        the selection against the peer's manifest — rather than trusting sizes and
        mtimes sent by a browser — is what keeps the resume sidecars honest."""
        batch = TransferBatch(
            id=uuid.uuid4().hex[:12],
            peer_device_id=peer_device_id,
            peer_name=peer_name,
            media_dir_id=media_dir_id,
            scope=scope,
            label=label,
            filters=filters or {},
            destination_root=Path(destination_root),
            collection_path=collection_path,
            included=set(include_paths or ()),
            excluded=set(exclude_paths or ()),
        )
        for manifest in files or []:
            batch.files.append(
                TransferFile(
                    relative_path=manifest["relative_path"],
                    name=manifest.get("name") or Path(manifest["relative_path"]).name,
                    size=int(manifest.get("size") or 0),
                    mtime=float(manifest.get("mtime") or 0),
                )
            )
        self._service._submit(self._admit(batch), timeout=10.0)
        return batch.id

    def snapshot(self, peer_device_id: Optional[str] = None) -> list[dict]:
        """Batches (newest first) as plain dicts for the status UI."""
        return self._service._submit(self._snapshot_async(peer_device_id), timeout=10.0)

    def cancel(self, batch_id: str) -> bool:
        return self._service._submit(self._cancel_async(batch_id), timeout=10.0)

    def allow_relay_overage(self, batch_id: str) -> bool:
        """The soft relay budget's continue-anyway."""
        return self._service._submit(self._allow_overage_async(batch_id), timeout=10.0)

    def delete(self, batch_id: str) -> bool:
        """Remove an inactive batch from the status UI. Active batches must be
        cancelled first so this never hides live work."""
        return self._service._submit(self._delete_async(batch_id), timeout=10.0)

    # ---- engine-loop side --------------------------------------------------

    async def _admit(self, batch: TransferBatch) -> None:
        batch.resume_event = asyncio.Event()
        self._batches[batch.id] = batch
        self._trim_finished()
        asyncio.ensure_future(self._run_batch(batch))

    async def _snapshot_async(self, peer_device_id: Optional[str]) -> list[dict]:
        rows = []
        for batch in self._batches.values():
            if peer_device_id is not None and batch.peer_device_id != peer_device_id:
                continue
            rows.append(self._describe(batch))
        rows.reverse()
        return rows

    async def _cancel_async(self, batch_id: str) -> bool:
        batch = self._batches.get(batch_id)
        if batch is None or not batch.active:
            return False
        batch.cancelled = True
        if batch.resume_event is not None:
            batch.resume_event.set()
        return True

    async def _allow_overage_async(self, batch_id: str) -> bool:
        batch = self._batches.get(batch_id)
        if batch is None or not batch.active:
            return False
        batch.relay_overage_allowed = True
        if batch.resume_event is not None:
            batch.resume_event.set()
        return True

    async def _delete_async(self, batch_id: str) -> bool:
        batch = self._batches.get(batch_id)
        if batch is None or batch.active:
            return False
        self._batches.pop(batch_id, None)
        return True

    def _describe(self, batch: TransferBatch) -> dict:
        files_done = sum(1 for f in batch.files if f.state in (FILE_DONE, FILE_SKIPPED))
        files_failed = sum(1 for f in batch.files if f.state == FILE_FAILED)
        bytes_total = sum(f.size for f in batch.files)
        bytes_done = sum(
            f.size if f.state in (FILE_DONE, FILE_SKIPPED) else f.bytes_done for f in batch.files
        )
        return {
            "id": batch.id,
            "peer_device_id": batch.peer_device_id,
            "peer_name": batch.peer_name,
            "label": batch.label or batch.scope or batch.collection_path or batch.media_dir_id,
            "state": batch.state,
            "path": batch.path,
            "active": batch.active,
            "paused_for_budget": batch.state == STATE_PAUSED_RELAY_BUDGET,
            "files_total": len(batch.files) or batch.total_expected,
            "files_done": files_done,
            "files_failed": files_failed,
            "bytes_total": bytes_total,
            "bytes_done": bytes_done,
            "relay_bytes": batch.relay_bytes,
            "relay_budget_bytes": RELAY_BATCH_BUDGET_BYTES,
            "active_files": [f.name for f in batch.files if f.state == FILE_ACTIVE][:3],
            "failed_files": [
                {"name": f.name, "error": f.error} for f in batch.files if f.state == FILE_FAILED
            ][:5],
            "error": batch.error,
            "created_at": batch.created_at,
            "finished_at": batch.finished_at,
        }

    def _trim_finished(self) -> None:
        finished = [b for b in self._batches.values() if not b.active]
        for batch in finished[: max(0, len(finished) - MAX_FINISHED_BATCHES_KEPT)]:
            self._batches.pop(batch.id, None)

    # ---- batch execution ---------------------------------------------------

    async def _run_batch(self, batch: TransferBatch) -> None:
        holder = _SessionHolder(lambda: self._service.open_peer_session(batch.peer_device_id))
        try:
            if not batch.files:
                await self._collect_files(batch, holder)
            if batch.cancelled:
                raise TransferAborted("cancelled")
            batch.state = STATE_RUNNING
            batch.path = holder.path

            semaphore = asyncio.Semaphore(TRANSFER_FILE_CONCURRENCY)

            async def pull_with_slot(entry: TransferFile) -> None:
                async with semaphore:
                    if batch.cancelled or batch.state == STATE_FAILED:
                        return
                    await self._download_one(holder, batch, entry)
                    batch.path = holder.path or batch.path

            results = await asyncio.gather(
                *(pull_with_slot(entry) for entry in batch.files), return_exceptions=True
            )
            abort = next((r for r in results if isinstance(r, TransferAborted)), None)
            unexpected = next((r for r in results if isinstance(r, BaseException)), None)
            if batch.cancelled:
                batch.state = STATE_CANCELLED
            elif abort is not None:
                batch.state = STATE_FAILED
                batch.error = str(abort)
            elif unexpected is not None:
                raise unexpected
            elif any(f.state == FILE_FAILED for f in batch.files):
                batch.state = STATE_COMPLETED_WITH_ERRORS
            else:
                batch.state = STATE_COMPLETED
        except TransferAborted as exc:
            batch.state = STATE_CANCELLED if batch.cancelled else STATE_FAILED
            if not batch.cancelled:
                batch.error = str(exc)
        except Exception as exc:  # noqa: BLE001 — a batch must never take the engine loop down
            logger.exception("transfer batch %s crashed", batch.id)
            batch.state = STATE_FAILED
            batch.error = str(exc)
        finally:
            if batch.state in (STATE_CANCELLED, STATE_FAILED):
                for entry in batch.files:
                    if entry.state in (FILE_QUEUED, FILE_ACTIVE):
                        entry.state = FILE_CANCELLED
            batch.finished_at = datetime.now(tz=timezone.utc)
            holder.close()
            logger.info(
                "transfer batch %s finished state=%s path=%s files=%d relay_bytes=%d",
                batch.id,
                batch.state,
                batch.path,
                len(batch.files),
                batch.relay_bytes,
            )

    async def _collect_files(self, batch: TransferBatch, holder: _SessionHolder) -> None:
        """Snapshot the manifest for the granted scope + filters by paging
        list_files over the session, keeping what the selection asks for. The batch
        pulls the snapshot, not a live query — files added on the peer mid-batch are
        simply not part of this batch."""
        offset = 0
        while True:
            if batch.cancelled:
                return
            payload = build_list_files_request(
                self._service.identity, batch.media_dir_id, batch.scope, batch.filters, offset, MAX_LIST_FILES_LIMIT
            )
            response = await holder.request(payload)
            if response.get("status") != "ok":
                raise TransferAborted(response.get("detail") or "peer refused to list files")
            batch.path = holder.path or batch.path
            for manifest in response.get("files", []):
                relative_path = manifest["relative_path"]
                if batch.included and relative_path not in batch.included:
                    continue  # an explicit selection: only these
                if relative_path in batch.excluded:
                    continue  # the whole scope, except these
                batch.files.append(
                    TransferFile(
                        relative_path=relative_path,
                        name=manifest.get("name") or Path(relative_path).name,
                        size=int(manifest.get("size") or 0),
                        mtime=float(manifest.get("mtime") or 0),
                    )
                )
            total = int(response.get("total") or 0)
            # What this batch will actually pull, not what the scope holds.
            batch.total_expected = len(batch.files) if (batch.included or batch.excluded) else total
            offset += int(response.get("limit") or MAX_LIST_FILES_LIMIT)
            if offset >= total or not response.get("files"):
                return

    # ---- one file ----------------------------------------------------------

    def _destination_for(self, batch: TransferBatch, clean_path: str) -> Path:
        root = batch.destination_root.expanduser().resolve()
        device_folder = safe_path_component(batch.peer_name or batch.peer_device_id, batch.peer_device_id)
        collection_folder = clean_destination_path(batch.collection_path or batch.media_dir_id)
        file_path = relative_path_inside_scope(clean_path, batch.scope)
        destination = root.joinpath(device_folder, *collection_folder.split("/"), *file_path.split("/")).resolve()
        if not path_inside(destination, root):
            raise ValueError("destination path escapes the download directory")
        return destination

    async def _download_one(self, holder: _SessionHolder, batch: TransferBatch, entry: TransferFile) -> None:
        try:
            clean_path = clean_relative_path(entry.relative_path)
            destination = self._destination_for(batch, clean_path)
        except ValueError as exc:
            entry.state = FILE_FAILED
            entry.error = str(exc)
            return

        if destination.exists() and entry.size and destination.stat().st_size == entry.size:
            entry.state = FILE_SKIPPED
            entry.bytes_done = entry.size
            return

        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f"{destination.name}.partial")
        sidecar = destination.with_name(f"{destination.name}.partial.json")
        entry.state = FILE_ACTIVE

        restarts = 0
        while True:
            outcome, detail = await self._pull_file_once(holder, batch, entry, clean_path, partial, sidecar)
            if outcome == "done":
                partial.replace(destination)
                sidecar.unlink(missing_ok=True)
                entry.state = FILE_DONE
                entry.error = None
                return
            if outcome == "failed":
                entry.state = FILE_FAILED
                entry.error = detail
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
                return

    def _sidecar_expectation(self, batch: TransferBatch, entry: TransferFile, clean_path: str) -> dict:
        return {
            "peer_device_id": batch.peer_device_id,
            "media_dir_id": batch.media_dir_id,
            "relative_path": clean_path,
            "size": entry.size,
            "mtime": entry.mtime,
        }

    def _prepare_resume(
        self, batch: TransferBatch, entry: TransferFile, clean_path: str, partial: Path, sidecar: Path
    ) -> tuple[int, "hashlib._Hash"]:
        """Decide the starting offset: resume the partial only when its
        sidecar matches the manifest we're pulling against (same source,
        same size/mtime); anything else restarts from zero. Returns the
        offset and a running sha256 primed with the resumed bytes, so the
        end-to-end check covers them too."""
        expectation = self._sidecar_expectation(batch, entry, clean_path)
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

    async def _pull_file_once(
        self,
        holder: _SessionHolder,
        batch: TransferBatch,
        entry: TransferFile,
        clean_path: str,
        partial: Path,
        sidecar: Path,
    ) -> tuple[str, Optional[str]]:
        """One attempt at pulling the file to completion. Returns
        ("done"|"failed"|"restart", detail)."""
        offset, hasher = self._prepare_resume(batch, entry, clean_path, partial, sidecar)
        entry.bytes_done = offset
        with partial.open("ab") as handle:
            while True:
                if batch.cancelled:
                    raise TransferAborted("cancelled")
                await self._respect_relay_budget(batch, holder)
                payload = build_pull_file_request(
                    self._service.identity, batch.media_dir_id, clean_path, offset, DEFAULT_PULL_CHUNK_BYTES
                )
                chunk = await holder.request(payload)
                if chunk.get("status") != "ok":
                    return "failed", chunk.get("detail") or "peer refused the file"

                problem = self._chunk_problem(chunk, batch.media_dir_id, clean_path, offset)
                if problem is not None:
                    return "failed", problem
                if entry.size and (chunk.get("size") != entry.size or self._mtime_changed(chunk, entry)):
                    # The source file changed since the manifest snapshot —
                    # appending would splice two versions. Adopt the new
                    # identity and restart.
                    entry.size = int(chunk.get("size") or 0)
                    entry.mtime = float(chunk.get("mtime") or 0)
                    return "restart", "the source file changed while it was being pulled"
                if not entry.size:
                    entry.size = int(chunk.get("size") or 0)
                    entry.mtime = float(chunk.get("mtime") or 0)
                    sidecar.write_text(json.dumps(self._sidecar_expectation(batch, entry, clean_path)))

                data = base64.b64decode(chunk["data_b64"])
                handle.write(data)
                handle.flush()
                hasher.update(data)
                offset = chunk["next_offset"]
                entry.bytes_done = offset
                batch.path = holder.path or batch.path
                await self._account_relay_bytes(batch, holder, len(data))

                if chunk.get("eof"):
                    expected_hash = chunk.get("file_sha256")
                    if expected_hash and hasher.hexdigest() != expected_hash:
                        return "restart", "the downloaded file did not match the peer's checksum"
                    return "done", None

    @staticmethod
    def _mtime_changed(chunk: dict, entry: TransferFile) -> bool:
        mtime = chunk.get("mtime")
        if mtime is None or not entry.mtime:
            return False  # older peers don't send mtime; size + final checksum still protect us
        return abs(float(mtime) - entry.mtime) > 1e-6

    @staticmethod
    def _chunk_problem(chunk: dict, media_dir_id: str, clean_path: str, offset: int) -> Optional[str]:
        if chunk.get("media_dir_id") != media_dir_id or chunk.get("relative_path") != clean_path:
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

    # ---- relay policy ------------------------------------------------------

    async def _respect_relay_budget(self, batch: TransferBatch, holder: _SessionHolder) -> None:
        """Soft budget: a batch that has pushed more than the budget through
        the relay pauses (all workers gather here) until the user either
        continues anyway or cancels. Never a hard block — hard-NAT users
        have no other path."""
        if holder.path != "relay" or batch.relay_overage_allowed:
            return
        if batch.relay_bytes < RELAY_BATCH_BUDGET_BYTES:
            return
        if batch.state == STATE_RUNNING:
            batch.state = STATE_PAUSED_RELAY_BUDGET
            batch.resume_event.clear()
            logger.info("transfer batch %s paused at the relay budget (%d bytes)", batch.id, batch.relay_bytes)
        await batch.resume_event.wait()
        if batch.cancelled:
            raise TransferAborted("cancelled")
        if batch.state == STATE_PAUSED_RELAY_BUDGET:
            batch.state = STATE_RUNNING

    async def _account_relay_bytes(self, batch: TransferBatch, holder: _SessionHolder, count: int) -> None:
        """Count relayed payload bytes and pace them: sleep just enough to
        keep the batch at or under RELAY_THROTTLE_BYTES_PER_SECOND. Direct
        and LAN paths are never throttled — their bytes are free."""
        if holder.path != "relay":
            return
        loop_time = asyncio.get_running_loop().time()
        if batch.relay_pace_started is None:
            batch.relay_pace_started = loop_time
        batch.relay_bytes += count
        expected_elapsed = batch.relay_bytes / RELAY_THROTTLE_BYTES_PER_SECOND
        actual_elapsed = loop_time - batch.relay_pace_started
        if expected_elapsed > actual_elapsed:
            await asyncio.sleep(min(expected_elapsed - actual_elapsed, 5.0))
