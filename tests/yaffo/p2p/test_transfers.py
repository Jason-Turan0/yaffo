"""The Phase 6 transfer engine, unit-tested against a scripted session
holder (no sockets): sidecar-driven resume, source-change restarts,
wedged-partial recovery, end-to-end checksum enforcement, download-all
manifest collection, and the soft relay budget. The engine's real transport
(one session, many chunks) is exercised end-to-end in
test_service_integration.py.
"""
import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace

import pytest

import yaffo.p2p.transfers as transfers_module
from yaffo.p2p.identity import InMemorySecretStore, load_or_create_identity
from yaffo.p2p.transfers import (
    FILE_DONE,
    FILE_FAILED,
    FILE_SKIPPED,
    STATE_PAUSED_RELAY_BUDGET,
    STATE_RUNNING,
    TransferAborted,
    TransferBatch,
    TransferFile,
    TransferManager,
)

pytestmark = pytest.mark.unit

PEER_ID = "PEER-DEVI-CEID-0001"
MTIME = 1720000000.0


class RemoteFile:
    def __init__(self, content: bytes, mtime: float = MTIME):
        self.content = content
        self.mtime = mtime


class ScriptedHolder:
    """Answers the engine's signed requests the way the real serving handler
    does, from an in-memory remote library."""

    # Media item ids the fake peer hands out, in manifest order (ids are the peer's
    # handles — the requester never sends a path).
    def _id_of(self, relative_path: str) -> int:
        return sorted(self.files).index(relative_path) + 1

    def _path_of(self, media_item_id: int) -> str | None:
        paths = sorted(self.files)
        index = int(media_item_id) - 1
        return paths[index] if 0 <= index < len(paths) else None

    def __init__(self, files: dict[str, RemoteFile], path: str = "local", tamper_eof_hash: bool = False):
        self.files = files
        self.path = path
        self.tamper_eof_hash = tamper_eof_hash
        self.requests: list[dict] = []
        self.on_request = None  # optional hook(payload) for cancel tests

    async def request(self, payload: dict, timeout: float = None) -> dict:
        self.requests.append(dict(payload))
        if self.on_request is not None:
            self.on_request(payload)
        if payload.get("type") == "pull_file":
            return self._chunk(payload)
        if payload.get("type") == "list_files":
            return self._page(payload)
        return {"status": "ok"}

    def _chunk(self, payload: dict) -> dict:
        relative_path = self._path_of(payload["media_item_id"])
        remote = self.files.get(relative_path) if relative_path else None
        if remote is None:
            return {"status": "error", "detail": "no active share grant covers this file"}
        offset, length = payload["offset"], payload["length"]
        data = remote.content[offset:offset + length]
        next_offset = offset + len(data)
        eof = next_offset >= len(remote.content)
        response = {
            "status": "ok",
            "type": "file_chunk",
            "media_item_id": payload["media_item_id"],
            "offset": offset,
            "next_offset": next_offset,
            "size": len(remote.content),
            "mtime": remote.mtime,
            "eof": eof,
            "bytes": len(data),
            "chunk_sha256": hashlib.sha256(data).hexdigest(),
            "data_b64": base64.b64encode(data).decode("ascii"),
        }
        if eof:
            digest = hashlib.sha256(remote.content).hexdigest()
            response["file_sha256"] = "0" * 64 if self.tamper_eof_hash else digest
        return response

    def _page(self, payload: dict) -> dict:
        manifests = [
            {
                "media_item_id": self._id_of(rel),
                "media_dir_id": "remote-lib",
                "relative_path": rel,
                "name": rel.rsplit("/", 1)[-1],
                "size": len(f.content),
                "mtime": f.mtime,
            }
            for rel, f in sorted(self.files.items())
        ]
        offset = payload["offset"]
        limit = 2  # small pages force the engine to actually paginate
        return {
            "status": "ok",
            "files": manifests[offset:offset + limit],
            "total": len(manifests),
            "offset": offset,
            "limit": limit,
        }

    def close(self) -> None:
        pass


@pytest.fixture
def identity():
    return load_or_create_identity(InMemorySecretStore())


@pytest.fixture
def manager(identity):
    return TransferManager(SimpleNamespace(identity=identity))


def make_batch(tmp_path, scope="trip", collection="trip", included=None, excluded=None) -> TransferBatch:
    return TransferBatch(
        id="t1",
        peer_device_id=PEER_ID,
        peer_name="laptop",
        media_dir_id="remote-lib",
        scope=scope,
        label="trip",
        filters={},
        destination_root=tmp_path / "downloads",
        collection_path=collection,
        included=set(included or ()),
        excluded=set(excluded or ()),
    )


def make_entry(relative_path="trip/a.jpg", size=6, mtime=MTIME, media_item_id=1) -> TransferFile:
    return TransferFile(
        media_item_id=media_item_id,
        relative_path=relative_path,
        name=relative_path.rsplit("/", 1)[-1],
        size=size,
        mtime=mtime,
    )


def destination_of(tmp_path, name="a.jpg"):
    return tmp_path / "downloads" / "laptop" / "trip" / name


def sidecar_json(size=6, mtime=MTIME, relative_path="trip/a.jpg") -> str:
    return json.dumps(
        {
            "peer_device_id": PEER_ID,
            "media_dir_id": "remote-lib",
            "relative_path": relative_path,
            "size": size,
            "mtime": mtime,
        }
    )


def pull_offsets(holder) -> list[int]:
    return [r["offset"] for r in holder.requests if r.get("type") == "pull_file"]


def run_download(manager, holder, batch, entry):
    asyncio.run(manager._download_one(holder, batch, entry))


def test_pull_completes_and_cleans_up_scratch_state(manager, tmp_path):
    holder = ScriptedHolder({"trip/a.jpg": RemoteFile(b"abcdef")})
    batch, entry = make_batch(tmp_path), make_entry()

    run_download(manager, holder, batch, entry)

    destination = destination_of(tmp_path)
    assert entry.state == FILE_DONE
    assert destination.read_bytes() == b"abcdef"
    assert not destination.with_name("a.jpg.partial").exists()
    assert not destination.with_name("a.jpg.partial.json").exists()


def test_multi_chunk_pull_advances_offsets(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(transfers_module, "DEFAULT_PULL_CHUNK_BYTES", 3)
    holder = ScriptedHolder({"trip/a.jpg": RemoteFile(b"abcdef")})
    batch, entry = make_batch(tmp_path), make_entry()

    run_download(manager, holder, batch, entry)

    assert pull_offsets(holder) == [0, 3]
    assert destination_of(tmp_path).read_bytes() == b"abcdef"


def test_resume_continues_from_matching_sidecar(manager, tmp_path, monkeypatch):
    """A partial whose sidecar matches the manifest resumes appending; the
    end-to-end hash still covers the resumed bytes."""
    monkeypatch.setattr(transfers_module, "DEFAULT_PULL_CHUNK_BYTES", 3)
    holder = ScriptedHolder({"trip/a.jpg": RemoteFile(b"abcdef")})
    batch, entry = make_batch(tmp_path), make_entry()
    destination = destination_of(tmp_path)
    destination.parent.mkdir(parents=True)
    destination.with_name("a.jpg.partial").write_bytes(b"abc")
    destination.with_name("a.jpg.partial.json").write_text(sidecar_json())

    run_download(manager, holder, batch, entry)

    assert pull_offsets(holder) == [3]
    assert entry.state == FILE_DONE
    assert destination.read_bytes() == b"abcdef"


def test_stale_sidecar_restarts_from_zero(manager, tmp_path, monkeypatch):
    """A sidecar recorded against a different manifest (other mtime) must
    NOT be appended to — that would splice two versions of the file."""
    monkeypatch.setattr(transfers_module, "DEFAULT_PULL_CHUNK_BYTES", 3)
    holder = ScriptedHolder({"trip/a.jpg": RemoteFile(b"abcdef")})
    batch, entry = make_batch(tmp_path), make_entry()
    destination = destination_of(tmp_path)
    destination.parent.mkdir(parents=True)
    destination.with_name("a.jpg.partial").write_bytes(b"OLD")
    destination.with_name("a.jpg.partial.json").write_text(sidecar_json(mtime=MTIME - 999))

    run_download(manager, holder, batch, entry)

    assert pull_offsets(holder)[0] == 0
    assert destination.read_bytes() == b"abcdef"


def test_source_changed_on_peer_restarts_with_new_manifest(manager, tmp_path, monkeypatch):
    """The peer's file changed after the browse snapshot: the first chunk's
    size/mtime disagree with the manifest, so the engine restarts against
    the file's new identity instead of splicing."""
    monkeypatch.setattr(transfers_module, "DEFAULT_PULL_CHUNK_BYTES", 3)
    holder = ScriptedHolder({"trip/a.jpg": RemoteFile(b"abcdef", mtime=MTIME + 100)})
    batch, entry = make_batch(tmp_path), make_entry(size=3, mtime=MTIME)

    run_download(manager, holder, batch, entry)

    assert entry.state == FILE_DONE
    assert entry.size == 6 and entry.mtime == MTIME + 100
    assert pull_offsets(holder) == [0, 0, 3]
    assert destination_of(tmp_path).read_bytes() == b"abcdef"


def test_corrupt_partial_recovers_instead_of_wedging(manager, tmp_path, monkeypatch):
    """Corrupt resumed bytes fail the eof checksum — the engine must throw
    the partial away and restart, not leave a poisoned partial that makes
    every retry fail forever."""
    monkeypatch.setattr(transfers_module, "DEFAULT_PULL_CHUNK_BYTES", 3)
    holder = ScriptedHolder({"trip/a.jpg": RemoteFile(b"abcdef")})
    batch, entry = make_batch(tmp_path), make_entry()
    destination = destination_of(tmp_path)
    destination.parent.mkdir(parents=True)
    destination.with_name("a.jpg.partial").write_bytes(b"XXX")  # wrong bytes, right length
    destination.with_name("a.jpg.partial.json").write_text(sidecar_json())

    run_download(manager, holder, batch, entry)

    assert entry.state == FILE_DONE
    assert pull_offsets(holder) == [3, 0, 3]
    assert destination.read_bytes() == b"abcdef"


def test_persistent_checksum_failure_fails_without_wedging(manager, tmp_path):
    holder = ScriptedHolder({"trip/a.jpg": RemoteFile(b"abcdef")}, tamper_eof_hash=True)
    batch, entry = make_batch(tmp_path), make_entry()

    run_download(manager, holder, batch, entry)

    destination = destination_of(tmp_path)
    assert entry.state == FILE_FAILED
    assert "checksum" in entry.error
    assert not destination.exists()
    assert not destination.with_name("a.jpg.partial").exists()
    assert not destination.with_name("a.jpg.partial.json").exists()


def test_existing_destination_is_skipped_without_requests(manager, tmp_path):
    holder = ScriptedHolder({"trip/a.jpg": RemoteFile(b"abcdef")})
    batch, entry = make_batch(tmp_path), make_entry()
    destination = destination_of(tmp_path)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"abcdef")

    run_download(manager, holder, batch, entry)

    assert entry.state == FILE_SKIPPED
    assert holder.requests == []


def test_peer_denial_marks_the_file_failed(manager, tmp_path):
    holder = ScriptedHolder({})
    batch, entry = make_batch(tmp_path), make_entry()

    run_download(manager, holder, batch, entry)

    assert entry.state == FILE_FAILED
    assert "no active share grant" in entry.error


def test_cancel_mid_pull_keeps_the_partial_for_a_later_resume(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(transfers_module, "DEFAULT_PULL_CHUNK_BYTES", 3)
    holder = ScriptedHolder({"trip/a.jpg": RemoteFile(b"abcdefghi")})
    batch, entry = make_batch(tmp_path), make_entry(size=9)

    def cancel_after_first_chunk(payload):
        if len(pull_offsets(holder)) == 2:
            batch.cancelled = True

    holder.on_request = cancel_after_first_chunk

    with pytest.raises(TransferAborted):
        run_download(manager, holder, batch, entry)

    destination = destination_of(tmp_path)
    # The chunk in flight when cancel landed still gets written — the
    # partial keeps every verified byte for a later resume.
    assert destination.with_name("a.jpg.partial").read_bytes() == b"abcdef"
    assert destination.with_name("a.jpg.partial.json").exists()
    assert not destination.exists()


def test_collect_files_pages_through_the_manifest(manager, tmp_path):
    holder = ScriptedHolder(
        {
            "trip/a.jpg": RemoteFile(b"aa"),
            "trip/b.jpg": RemoteFile(b"bbb"),
            "trip/c.jpg": RemoteFile(b"cccc"),
        }
    )
    batch = make_batch(tmp_path)

    asyncio.run(manager._collect_files(batch, holder))

    assert [f.relative_path for f in batch.files] == ["trip/a.jpg", "trip/b.jpg", "trip/c.jpg"]
    assert [f.size for f in batch.files] == [2, 3, 4]
    assert batch.total_expected == 3
    list_offsets = [r["offset"] for r in holder.requests if r.get("type") == "list_files"]
    assert list_offsets == [0, 2]


def test_collect_files_keeps_only_the_selected_items(manager, tmp_path):
    """An explicit selection in the gallery: the browser sends the PEER's media item ids,
    and the batch takes their manifests (size/mtime — the resume-sidecar seed) from the
    peer, never from the browser."""
    holder = ScriptedHolder(
        {
            "trip/a.jpg": RemoteFile(b"aa"),
            "trip/b.jpg": RemoteFile(b"bbb"),
            "trip/c.jpg": RemoteFile(b"cccc"),
        }
    )
    batch = make_batch(tmp_path, included=[1, 3])  # a.jpg and c.jpg, by the peer's ids

    asyncio.run(manager._collect_files(batch, holder))

    assert [f.relative_path for f in batch.files] == ["trip/a.jpg", "trip/c.jpg"]
    assert [f.size for f in batch.files] == [2, 4]  # authoritative, from the peer
    assert batch.total_expected == 2  # what this batch will pull, not the scope size


def test_collect_files_drops_the_excluded_items(manager, tmp_path):
    """"Select all, except these": the scope is snapshotted whole and the exclusions
    are removed from it — so files on pages the user never rendered are still pulled."""
    holder = ScriptedHolder(
        {
            "trip/a.jpg": RemoteFile(b"aa"),
            "trip/b.jpg": RemoteFile(b"bbb"),
            "trip/c.jpg": RemoteFile(b"cccc"),
        }
    )
    batch = make_batch(tmp_path, excluded=[2])  # b.jpg, by the peer's id

    asyncio.run(manager._collect_files(batch, holder))

    assert [f.relative_path for f in batch.files] == ["trip/a.jpg", "trip/c.jpg"]
    assert batch.total_expected == 2


def test_relay_budget_pauses_and_continue_anyway_resumes(manager, tmp_path):
    async def scenario():
        batch = make_batch(tmp_path)
        batch.resume_event = asyncio.Event()
        batch.state = STATE_RUNNING
        batch.relay_bytes = transfers_module.RELAY_BATCH_BUDGET_BYTES + 1
        manager._batches[batch.id] = batch
        holder = SimpleNamespace(path="relay")

        waiter = asyncio.ensure_future(manager._respect_relay_budget(batch, holder))
        await asyncio.sleep(0.01)
        assert batch.state == STATE_PAUSED_RELAY_BUDGET
        assert not waiter.done()

        assert await manager._allow_overage_async(batch.id) is True
        await asyncio.wait_for(waiter, timeout=2)
        assert batch.state == STATE_RUNNING

        # Once overage is allowed, the budget never pauses this batch again.
        await manager._respect_relay_budget(batch, holder)

    asyncio.run(scenario())


def test_relay_budget_ignores_free_paths(manager, tmp_path):
    async def scenario():
        batch = make_batch(tmp_path)
        batch.resume_event = asyncio.Event()
        batch.state = STATE_RUNNING
        batch.relay_bytes = transfers_module.RELAY_BATCH_BUDGET_BYTES * 2
        await manager._respect_relay_budget(batch, SimpleNamespace(path="direct"))
        assert batch.state == STATE_RUNNING

    asyncio.run(scenario())


def test_delete_only_removes_inactive_batches(manager, tmp_path):
    async def scenario():
        active = make_batch(tmp_path)
        active.id = "active"
        active.state = STATE_RUNNING
        inactive = make_batch(tmp_path)
        inactive.id = "inactive"
        inactive.state = "cancelled"
        manager._batches = {active.id: active, inactive.id: inactive}

        assert await manager._delete_async(active.id) is False
        assert "active" in manager._batches

        assert await manager._delete_async(inactive.id) is True
        assert "inactive" not in manager._batches

    asyncio.run(scenario())
