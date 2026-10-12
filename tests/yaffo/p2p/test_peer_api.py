"""The engine side of the internal P2P API, against scripted peer sessions (no
sockets): one shared session per peer, relay policy, idle expiry, pulls with
resume, long-polls, cancelling, and the error codes failures map onto."""
import asyncio
import base64
import hashlib
from types import SimpleNamespace

import pytest

import yaffo.p2p.peer_api as peer_api_module
import yaffo.p2p.puller as puller_module
from yaffo.p2p.identity import InMemorySecretStore, load_or_create_identity
from yaffo.p2p.peer_api import PeerApi, PeerApiError, Scope, files_page
from yaffo.p2p.puller import PeerSession
from yaffo.p2p.signaling import CallError

pytestmark = pytest.mark.unit

PEER = "PEER-0000-0000-0001"
CONTENT = b"0123456789abcdef"
MTIME = 1720000000.0


class ScriptedPeer:
    """Answers pull_file and list_files like the real serving handlers."""

    def __init__(self, content=CONTENT, path="local", refuse=None, gate=None, drift=False):
        self.content = content
        self.drift = drift  # the file is rewritten before every attempt (a new mtime at offset 0)
        self.mtime = MTIME
        self.path = path
        self.refuse = refuse
        self.gate = gate  # an asyncio.Event a pull waits on, to hold it mid-flight
        self.offsets = []
        self.closed = False

    async def request(self, payload, timeout):
        if payload["type"] == "list_files":
            if self.refuse:
                return {"status": "error", "detail": self.refuse}
            return {"status": "ok", "total": 1, "files": [
                {"media_item_id": 7, "media_dir_id": "md", "relative_path": "a.jpg", "size": len(self.content)}]}
        if self.gate is not None:
            await self.gate.wait()
        if self.refuse:
            return {"status": "error", "detail": self.refuse}
        offset = payload["offset"]
        self.offsets.append(offset)
        if self.drift and offset == 0:
            self.mtime += 1
        data = self.content[offset:offset + payload["length"]]
        eof = offset + len(data) >= len(self.content)
        chunk = {
            "status": "ok", "media_item_id": payload["media_item_id"], "offset": offset,
            "next_offset": offset + len(data), "size": len(self.content), "mtime": self.mtime, "eof": eof,
            "bytes": len(data), "chunk_sha256": hashlib.sha256(data).hexdigest(),
            "data_b64": base64.b64encode(data).decode(),
        }
        if eof:
            chunk["file_sha256"] = hashlib.sha256(self.content).hexdigest()
        return chunk

    def close(self):
        self.closed = True


class FakeService:
    def __init__(self, peer=None, fail_open=None):
        self.identity = load_or_create_identity(InMemorySecretStore())
        self.peer = peer or ScriptedPeer()
        self.fail_open = fail_open
        self.opens = 0

    async def open_peer_session(self, peer_device_id):
        self.opens += 1
        if self.fail_open is not None:
            raise self.fail_open
        return PeerSession(self.peer.path, self.peer)


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch):
    monkeypatch.setattr(puller_module, "DEFAULT_PULL_CHUNK_BYTES", 4)


def run(coro_fn):
    return asyncio.run(coro_fn())


async def open_session(api, relay="never", budget=0, owner="sync-1"):
    return await api._open_session(PEER, relay, budget, owner)


async def finish(api, pull_id):
    for _ in range(50):
        status = await api._pull_status(pull_id, wait=1.0)
        if status["state"] in ("done", "failed", "cancelled"):
            return status
    raise AssertionError("pull never finished")


def destination_in(tmp_path):
    return tmp_path / ".yaffo-1-abc.part"


# ---- sessions -----------------------------------------------------------------


def test_opening_reports_the_path():
    api = PeerApi(FakeService())

    async def scenario():
        return await open_session(api)

    session = run(scenario)
    assert session["path"] == "lan"
    assert session["idle_expires_in"] == 120


def test_two_owners_share_one_session_to_a_peer():
    service = FakeService()
    api = PeerApi(service)

    async def scenario():
        first = await open_session(api, owner="sync-1")
        second = await open_session(api, owner="sync-2")
        await api._close_session(first["session_id"], "sync-1")
        still_open = await api._session_info(first["session_id"])
        await api._close_session(first["session_id"], "sync-2")
        return first, second, still_open

    first, second, still_open = run(scenario)
    assert first["session_id"] == second["session_id"]
    assert still_open["session_id"] == first["session_id"]
    assert service.opens == 1
    assert service.peer.closed


def test_a_relay_only_path_is_refused_when_relay_is_never():
    service = FakeService(peer=ScriptedPeer(path="relay"))
    api = PeerApi(service)

    async def scenario():
        with pytest.raises(PeerApiError) as caught:
            await open_session(api, relay="never")
        return caught.value

    error = run(scenario)
    assert error.code == "relay_not_allowed" and error.status == 409
    assert service.peer.closed


def test_a_relayed_session_is_allowed_with_a_budget():
    api = PeerApi(FakeService(peer=ScriptedPeer(path="relay")))

    async def scenario():
        return await open_session(api, relay="budget", budget=1000)

    assert run(scenario)["path"] == "relay"


def test_an_unreachable_peer_is_offline():
    api = PeerApi(FakeService(fail_open=CallError("not connected to the hub")))

    async def scenario():
        with pytest.raises(PeerApiError) as caught:
            await open_session(api)
        return caught.value

    assert run(scenario).code == "peer_offline"


def test_idle_sessions_expire(monkeypatch):
    service = FakeService()
    api = PeerApi(service)

    async def scenario():
        session = await open_session(api)
        api._sessions[session["session_id"]].last_used -= peer_api_module.IDLE_EXPIRY_SECONDS + 1
        api._reap()
        with pytest.raises(PeerApiError) as caught:
            await api._session_info(session["session_id"])
        return caught.value

    assert run(scenario).code == "session_expired"
    assert service.peer.closed


# ---- files ----------------------------------------------------------------------


def test_files_over_a_session_carry_source_ids():
    api = PeerApi(FakeService())

    async def scenario():
        session = await open_session(api)
        return await api._list_files(session["session_id"], Scope(media_dir_id="md"), 0, 200)

    page = run(scenario)
    assert page["files"][0]["source_id"] == "md:a.jpg"
    assert page["next_cursor"] is None


def test_a_missing_file_does_not_end_the_listing_early():
    first = files_page({"total": 5, "limit": 2, "files": []}, cursor=0)
    second = files_page({"total": 5, "limit": 2, "files": [{"media_dir_id": "md", "relative_path": "c.jpg"}]},
                        cursor=int(first["next_cursor"]))
    last = files_page({"total": 5, "limit": 2, "files": [{"media_dir_id": "md", "relative_path": "e.jpg"}]},
                      cursor=int(second["next_cursor"]))

    assert first["next_cursor"] == "2"
    assert second["files"][0]["source_id"] == "md:c.jpg"
    assert second["next_cursor"] == "4"
    assert last["next_cursor"] is None


def test_a_peer_that_stopped_trusting_us():
    api = PeerApi(FakeService(peer=ScriptedPeer(refuse="SELF is not a trusted device — pair first")))

    async def scenario():
        session = await open_session(api)
        with pytest.raises(PeerApiError) as caught:
            await api._list_files(session["session_id"], Scope(media_dir_id="md"), 0, 200)
        return caught.value

    assert run(scenario).code == "not_trusted"


# ---- pulls ----------------------------------------------------------------------


def start(api, session_id, destination, size=len(CONTENT)):
    return api._start_pull(session_id, 7, "md:a.jpg", size, MTIME, destination)


def test_a_pull_writes_the_destination_and_reports_its_hash(tmp_path):
    api = PeerApi(FakeService())
    destination = destination_in(tmp_path)

    async def scenario():
        session = await open_session(api)
        pull = await start(api, session["session_id"], destination)
        return await finish(api, pull["pull_id"])

    status = run(scenario)
    assert status["state"] == "done"
    assert status["sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert destination.read_bytes() == CONTENT
    assert not destination.with_name(destination.name + ".partial").exists()


def test_a_pull_resumes_a_matching_partial(tmp_path):
    service = FakeService()
    api = PeerApi(service)
    destination = destination_in(tmp_path)
    partial = destination.with_name(destination.name + ".partial")
    partial.write_bytes(CONTENT[:8])
    destination.with_name(destination.name + ".partial.json").write_text(
        '{"peer_device_id": "%s", "source_id": "md:a.jpg", "size": %d, "mtime": %r}'
        % (PEER, len(CONTENT), MTIME)
    )

    async def scenario():
        session = await open_session(api)
        pull = await start(api, session["session_id"], destination)
        return await finish(api, pull["pull_id"])

    assert run(scenario)["state"] == "done"
    assert service.peer.offsets[0] == 8
    assert destination.read_bytes() == CONTENT


@pytest.mark.parametrize(("detail", "code"), [
    ("no active share grant covers this file", "file_refused"),
    ("SELF is not a trusted device — pair first", "not_trusted"),
])
def test_a_refused_file_maps_to_a_code(tmp_path, detail, code):
    api = PeerApi(FakeService(peer=ScriptedPeer(refuse=detail)))

    async def scenario():
        session = await open_session(api)
        pull = await start(api, session["session_id"], destination_in(tmp_path))
        return await finish(api, pull["pull_id"])

    status = run(scenario)
    assert status["state"] == "failed"
    assert status["error"]["code"] == code


def test_a_file_that_keeps_changing_is_source_changed(tmp_path):
    api = PeerApi(FakeService(peer=ScriptedPeer(drift=True)))

    async def scenario():
        session = await open_session(api)
        pull = await start(api, session["session_id"], destination_in(tmp_path))
        return await finish(api, pull["pull_id"])

    status = run(scenario)
    assert status["state"] == "failed"
    assert status["error"]["code"] == "source_changed"


def test_the_relay_budget_stops_unattended_pulls(tmp_path):
    api = PeerApi(FakeService(peer=ScriptedPeer(path="relay")))

    async def scenario():
        session = await open_session(api, relay="budget", budget=4)
        pull = await start(api, session["session_id"], destination_in(tmp_path))
        return await finish(api, pull["pull_id"])

    status = run(scenario)
    assert status["state"] == "failed"
    assert status["error"]["code"] == "relay_budget_reached"
    assert destination_in(tmp_path).with_name(".yaffo-1-abc.part.partial").stat().st_size == 4


def test_the_long_poll_returns_when_the_state_changes(tmp_path):
    gate_holder = {}

    async def scenario():
        gate = asyncio.Event()
        gate_holder["gate"] = gate
        api = PeerApi(FakeService(peer=ScriptedPeer(gate=gate)))
        session = await open_session(api)
        pull = await start(api, session["session_id"], destination_in(tmp_path))
        active = await api._pull_status(pull["pull_id"], wait=5.0)
        loop = asyncio.get_running_loop()
        started = loop.time()
        timed_out = await api._pull_status(pull["pull_id"], wait=0.2)
        waited = loop.time() - started
        gate.set()
        done = await finish(api, pull["pull_id"])
        return active, timed_out, waited, done

    active, timed_out, waited, done = run(scenario)
    assert active["state"] == "active"
    assert timed_out["state"] == "active" and waited >= 0.19
    assert done["state"] == "done"


def test_cancelling_keeps_the_partial(tmp_path):
    async def scenario():
        gate = asyncio.Event()
        api = PeerApi(FakeService(peer=ScriptedPeer(gate=gate)))
        session = await open_session(api)
        pull = await start(api, session["session_id"], destination_in(tmp_path))
        await api._pull_status(pull["pull_id"], wait=5.0)
        await api._cancel_pull(pull["pull_id"])
        return await finish(api, pull["pull_id"])

    assert run(scenario)["state"] == "cancelled"
    assert destination_in(tmp_path).with_name(".yaffo-1-abc.part.partial").exists()


def test_closing_the_session_cancels_its_pulls(tmp_path):
    async def scenario():
        api = PeerApi(FakeService(peer=ScriptedPeer(gate=asyncio.Event())))
        session = await open_session(api)
        pull = await start(api, session["session_id"], destination_in(tmp_path))
        await api._pull_status(pull["pull_id"], wait=5.0)
        await api._close_session(session["session_id"], None)
        return await finish(api, pull["pull_id"])

    assert run(scenario)["state"] == "cancelled"


def test_pulls_beyond_the_session_concurrency_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(peer_api_module, "SESSION_PULL_CONCURRENCY", 1)

    async def scenario():
        gate = asyncio.Event()
        api = PeerApi(FakeService(peer=ScriptedPeer(gate=gate)))
        session = await open_session(api)
        first = await start(api, session["session_id"], tmp_path / ".yaffo-1-a.part")
        second = await start(api, session["session_id"], tmp_path / ".yaffo-1-b.part")
        await api._pull_status(first["pull_id"], wait=5.0)
        queued = await api._pull_status(second["pull_id"], wait=0)
        gate.set()
        return queued, await finish(api, first["pull_id"]), await finish(api, second["pull_id"])

    queued, first, second = run(scenario)
    assert queued["state"] == "queued"
    assert first["state"] == second["state"] == "done"


def test_unknown_pulls_have_expired():
    api = PeerApi(SimpleNamespace())

    async def scenario():
        with pytest.raises(PeerApiError) as caught:
            await api._pull_status("p_gone", wait=0)
        return caught.value

    assert run(scenario).code == "session_expired"


def test_a_running_pull_to_the_same_destination_is_reused(tmp_path):
    """A read-ahead pull left running by an ended run must not get a second writer."""
    async def scenario():
        gate = asyncio.Event()
        api = PeerApi(FakeService(peer=ScriptedPeer(gate=gate)))
        session = await open_session(api)
        first = await start(api, session["session_id"], destination_in(tmp_path))
        second = await start(api, session["session_id"], destination_in(tmp_path))
        gate.set()
        await finish(api, first["pull_id"])
        third = await start(api, session["session_id"], destination_in(tmp_path))
        return first, second, third

    first, second, third = run(scenario)
    assert first == second
    assert third != first  # a finished pull isn't reused
