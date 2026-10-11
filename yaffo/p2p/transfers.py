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

Pulling each file (chunking, resume, checksums, restarts) is PeerPuller's job
(yaffo/p2p/puller.py); this module adds what the remote gallery needs on top:
batches, manifest collection, selection, the {Device}/{Collection}/{path}
layout, and the interactive "continue anyway" relay budget.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from yaffo.logging_config import get_logger
from yaffo.p2p.handlers.list_files import build_list_files_request
from yaffo.p2p.handlers.sharing import (
    MAX_LIST_FILES_LIMIT,
    clean_destination_path,
    clean_relative_path,
    path_inside,
    relative_path_inside_scope,
    safe_path_component,
)
from yaffo.p2p.puller import (
    FILE_ACTIVE,
    FILE_CANCELLED,
    FILE_DONE,
    FILE_FAILED,
    FILE_QUEUED,
    FILE_SKIPPED,
    PeerPuller,
    SessionHolder,
    TransferAborted,
    pace_relay_bytes,
)
from yaffo.runtime_mode import demo_mode_enabled

logger = get_logger(__name__, "webapp")

# Concurrent files per batch — as parallel streams on the ONE session
# connection (which punches once and shares congestion control), never as
# parallel connections.
TRANSFER_FILE_CONCURRENCY = 3
# Relay policy: discourage, don't forbid. Relayed sessions are paced (see
# puller.RELAY_THROTTLE_BYTES_PER_SECOND) and a batch that pushes more than the
# budget through the relay pauses until the user explicitly continues.
RELAY_BATCH_BUDGET_BYTES = 1 * 2**30
# Finished batches kept for the status UI.
MAX_FINISHED_BATCHES_KEPT = 20
DEMO_TRANSFER_MAX_FILES = int(os.environ.get("YAFFO_DEMO_TRANSFER_MAX_FILES", "5"))
DEMO_TRANSFER_MAX_BATCH_BYTES = int(
    os.environ.get("YAFFO_DEMO_TRANSFER_MAX_BATCH_BYTES", str(25 * 1024 * 1024))
)
DEMO_DOWNLOAD_VOLUME_BYTES = int(
    os.environ.get("YAFFO_DEMO_DOWNLOAD_VOLUME_BYTES", str(100 * 1024 * 1024))
)

STATE_COLLECTING = "collecting"
STATE_RUNNING = "running"
STATE_PAUSED_RELAY_BUDGET = "paused_relay_budget"
STATE_COMPLETED = "completed"
STATE_COMPLETED_WITH_ERRORS = "completed_with_errors"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
ACTIVE_STATES = (STATE_COLLECTING, STATE_RUNNING, STATE_PAUSED_RELAY_BUDGET)


@dataclass
class TransferFile:
    # The peer's media item id: what a pull request names. The relative path travels as
    # DATA — it decides where the file lands and keys the resume sidecar — but it is
    # never what the file is requested BY.
    media_item_id: int
    relative_path: str
    name: str
    size: int
    mtime: float
    state: str = FILE_QUEUED
    bytes_done: int = 0
    error: Optional[str] = None
    problem: Optional[str] = None
    sha256: Optional[str] = None


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
    # Album-scoped batch: the peer lists the album's MEMBERS instead of a path scope.
    # Membership is resolved by the peer at request time, so a batch pulls whatever the
    # album holds when it runs.
    album_id: Optional[int] = None
    # A selection over the scope, resolved against the peer's manifest (see
    # _collect_files), as MEDIA ITEM IDS. `included` empty means "everything matching
    # the filters"; `excluded` removes items from whatever that yields. Sizes and mtimes
    # always come from the peer's manifest — they seed the resume sidecars, so they must
    # come from the source of truth, never from a browser.
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
        include_ids: Optional[list[int]] = None,
        exclude_ids: Optional[list[int]] = None,
        album_id: Optional[int] = None,
    ) -> str:
        """Enqueue a batch and return its id immediately.

        With explicit `files` (manifest dicts) those are pulled. Otherwise the batch
        collects the manifest for the scope+filters from the peer and applies the
        selection to it: `include_ids` keeps only those media items, and `exclude_ids`
        drops them ("everything matching, except these"). Resolving the selection
        against the peer's manifest — rather than trusting sizes and mtimes sent by a
        browser — is what keeps the resume sidecars honest."""
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
            album_id=album_id,
            included=set(include_ids or ()),
            excluded=set(exclude_ids or ()),
        )
        for manifest in files or []:
            batch.files.append(
                TransferFile(
                    media_item_id=int(manifest["media_item_id"]),
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
        if demo_mode_enabled() and any(existing.active for existing in self._batches.values()):
            raise ValueError("the public demo already has an active transfer")
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
        holder = SessionHolder(lambda: self._service.open_peer_session(batch.peer_device_id))
        try:
            if not batch.files:
                await self._collect_files(batch, holder)
            self._validate_demo_batch(batch)
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

    def _validate_demo_batch(self, batch: TransferBatch) -> None:
        if not demo_mode_enabled():
            return
        if len(batch.files) > DEMO_TRANSFER_MAX_FILES:
            raise TransferAborted(
                f"demo transfers are limited to {DEMO_TRANSFER_MAX_FILES} files"
            )
        batch_bytes = sum(max(0, entry.size) for entry in batch.files)
        if batch_bytes > DEMO_TRANSFER_MAX_BATCH_BYTES:
            raise TransferAborted("the selected files exceed the demo transfer-size limit")
        existing_bytes = sum(
            path.stat().st_size
            for path in batch.destination_root.rglob("*")
            if path.is_file()
        ) if batch.destination_root.exists() else 0
        if existing_bytes + batch_bytes > DEMO_DOWNLOAD_VOLUME_BYTES:
            raise TransferAborted("the demo download directory has reached its quota")

    async def _collect_files(self, batch: TransferBatch, holder: SessionHolder) -> None:
        """Snapshot the manifest for the granted scope + filters by paging
        list_files over the session, keeping what the selection asks for. The batch
        pulls the snapshot, not a live query — files added on the peer mid-batch are
        simply not part of this batch."""
        offset = 0
        while True:
            if batch.cancelled:
                return
            payload = build_list_files_request(
                self._service.identity,
                batch.media_dir_id,
                batch.scope,
                batch.filters,
                offset,
                MAX_LIST_FILES_LIMIT,
                album_id=batch.album_id,
            )
            response = await holder.request(payload)
            if response.get("status") != "ok":
                raise TransferAborted(response.get("detail") or "peer refused to list files")
            batch.path = holder.path or batch.path
            for manifest in response.get("files", []):
                media_item_id = int(manifest["media_item_id"])
                relative_path = manifest["relative_path"]
                if batch.included and media_item_id not in batch.included:
                    continue  # an explicit selection: only these
                if media_item_id in batch.excluded:
                    continue  # the whole scope, except these
                batch.files.append(
                    TransferFile(
                        media_item_id=media_item_id,
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

    async def _download_one(self, holder: SessionHolder, batch: TransferBatch, entry: TransferFile) -> None:
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

        async def before_chunk(session_holder: SessionHolder) -> None:
            await self._respect_relay_budget(batch, session_holder)

        async def after_chunk(session_holder: SessionHolder, count: int) -> None:
            batch.path = session_holder.path or batch.path
            await self._account_relay_bytes(batch, session_holder, count)

        puller = PeerPuller(
            self._service.identity,
            holder,
            before_chunk=before_chunk,
            after_chunk=after_chunk,
            is_cancelled=lambda: batch.cancelled,
        )
        source = {
            "peer_device_id": batch.peer_device_id,
            "media_dir_id": batch.media_dir_id,
            "relative_path": clean_path,
        }
        await puller.pull(entry, destination, source)

    # ---- relay policy ------------------------------------------------------

    async def _respect_relay_budget(self, batch: TransferBatch, holder: SessionHolder) -> None:
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

    async def _account_relay_bytes(self, batch: TransferBatch, holder: SessionHolder, count: int) -> None:
        """Count relayed payload bytes and pace them. Direct and LAN paths are
        never throttled — their bytes are free."""
        if holder.path != "relay":
            return
        await pace_relay_bytes(batch, count)
