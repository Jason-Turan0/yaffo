"""The peer source through the real sync engine, against an in-memory internal P2P
API: records and layout, dedupe before download, lazy sessions, read-ahead,
resumable temp names, and what each peer problem does to the run."""
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from yaffo.db.models import (
    AutomationTrigger, SyncAdapter, SyncItem,
    EVENT_DEVICE_ONLINE, SYNC_HANDLER_PEER_SOURCE, SYNC_ITEM_IMPORTED, SYNC_ITEM_LINKED_EXISTING, SYNC_ITEM_SKIPPED,
)
from yaffo.db.repositories import sync_repository
from yaffo.sync import adapters, peer_source, setup
from yaffo.sync.engine import run_sync
from yaffo.sync.files import TEMP_PREFIX, TEMP_SUFFIX, file_sha1
from yaffo.sync.peer_client import PeerClientError
from yaffo.sync.preview import preview_sync
from yaffo.sync.schemas import CURSOR_LIST, PAUSE_DEVICE_REVOKED, PAUSE_OPTION_MISSING, RUNTIME_PAUSED
from yaffo.utils import job_codes as codes

from .conftest import MEDIA_DIR_ID, write_jpeg

pytestmark = pytest.mark.unit

DEVICE = {"id": "MOMS-0000-0000-0001", "label": "Mom's laptop"}
SCOPE = {"id": "folder:md1:Vacation 2026", "label": "Pictures › Vacation 2026"}


class FakePeer:
    """The internal API as a peer source sees it, over an in-memory library."""

    def __init__(self, tmp_path: Path):
        self.files: dict[str, dict] = {}
        self.device = {"device_id": DEVICE["id"], "display_name": DEVICE["label"], "trust_state": "trusted",
                       "online": True, "local": False}
        self.available = True
        self.page_size = 200
        self.fail_files = None          # an error code for every /files call
        self.fail_open = None           # an error code for opening a session
        self.refuse = set()             # relative paths whose pull fails file_refused
        self.fail_pull = {}             # relative path -> error code
        self.opens = 0
        self.closed = []
        self.started = []               # (relative_path, destination) in start order
        self.polled = []                # relative paths, in the order their status was read
        self.cancelled = []
        self.filters_seen = []          # the filters of every /files call
        self.pulls = {}
        self.photo_dir = tmp_path / "peer-photos"

    def add(self, relative_path: str, color=(10, 20, 30), sha1=True, media_item_id=None):
        path = write_jpeg(self.photo_dir / relative_path, color=color)
        content = path.read_bytes()
        self.files[relative_path] = {
            "media_item_id": media_item_id or len(self.files) + 1, "media_dir_id": "md1",
            "relative_path": relative_path, "name": Path(relative_path).name, "size": len(content),
            "mtime": 1720000000.0, "media_type": "photo", "date_taken": "2024-07-04T18:22:10",
            "sha1": hashlib.sha1(content).hexdigest() if sha1 else None, "content": content,
            "source_id": f"md1:{relative_path}",
        }

    # ---- the client interface ----

    def status(self):
        if not self.available:
            return {"available": False}
        return {"available": True}

    def devices(self):
        return [self.device]

    def scopes(self, device_id):
        return {"protocol": 2, "scopes": [{"scope_key": SCOPE["id"], "scope_type": "folder", "media_dir_id": "md1",
                                           "relative_path": "Vacation 2026", "name": "Pictures", "file_count": 1}]}

    def files_page(self, device_id, scope_key, cursor=None, limit=200, session_id=None, filters=None):
        if self.fail_files:
            raise PeerClientError(self.fail_files, self.fail_files)
        self.filters_seen.append(filters)
        manifests = [{k: v for k, v in f.items() if k != "content"} for _, f in sorted(self.files.items())
                     if not (filters or {}).get("path") or filters["path"] in f["relative_path"]]
        start = int(cursor or 0)
        page = manifests[start:start + min(limit, self.page_size)]
        following = start + len(page)
        return {"files": page, "next_cursor": str(following) if following < len(manifests) else None,
                "total": len(manifests)}

    def open_session(self, device_id, owner, relay="never", relay_budget_bytes=None):
        self.opens += 1
        self.session_args = (device_id, owner, relay, relay_budget_bytes)
        if self.fail_open:
            raise PeerClientError(self.fail_open, self.fail_open)
        return {"session_id": "s_1", "path": "lan"}

    def close_session(self, session_id, owner=None):
        self.closed.append((session_id, owner))

    def _by_id(self, media_item_id):
        return next(f for f in self.files.values() if f["media_item_id"] == media_item_id)

    def start_pull(self, session_id, media_item_id, source_id, size, mtime, destination):
        manifest = self._by_id(media_item_id)
        pull_id = f"p_{len(self.started) + 1}"
        self.started.append((manifest["relative_path"], destination))
        self.pulls[pull_id] = (manifest, destination)
        return pull_id

    def pull(self, pull_id, wait=0):
        manifest, destination = self.pulls[pull_id]
        self.polled.append(manifest["relative_path"])
        code = self.fail_pull.get(manifest["relative_path"])
        if manifest["relative_path"] in self.refuse:
            code = "file_refused"
        if code:
            return {"state": "failed", "error": {"code": code, "message": code}}
        Path(destination).write_bytes(manifest["content"])
        return {"state": "done", "sha256": hashlib.sha256(manifest["content"]).hexdigest()}

    def cancel_pull(self, pull_id):
        self.cancelled.append(pull_id)


class FakeClientAdapter:
    """PeerClient's method names over a FakePeer (files is a method there)."""

    def __init__(self, peer: FakePeer):
        self._peer = peer

    def __getattr__(self, name):
        return getattr(self._peer, "files_page" if name == "files" else name)


@pytest.fixture
def peer(tmp_path, session, monkeypatch):
    fake = FakePeer(tmp_path)
    session.add(SyncAdapter(id=3, slug="builtin.peer_source", name="Paired device", role="source",
                            provider="peer", is_preset=True, handler=SYNC_HANDLER_PEER_SOURCE, grants=[],
                            doc_domains=[]))
    session.commit()
    builtin = adapters.BUILTINS[SYNC_HANDLER_PEER_SOURCE]
    monkeypatch.setitem(adapters.BUILTINS, SYNC_HANDLER_PEER_SOURCE, adapters.BuiltinAdapter(
        builtin.role, builtin.options,
        lambda: peer_source.PeerSource(client_factory=lambda: FakeClientAdapter(fake))))
    return fake


@pytest.fixture
def make_sync(session, library, peer):
    def make(layout="source_folders", **options):
        automation = setup.create_sync(session, "From Mom", 3)
        setup.set_import_root(session, automation, MEDIA_DIR_ID, "Imports/Mom", layout)
        setup.set_options(session, automation, {"device": DEVICE, "scope": SCOPE, **options})
        setup.turn_on(session, automation)
        return automation
    return make


def imported(library: Path) -> list[str]:
    root = library / "Imports" / "Mom"
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_imports_the_scope_keeping_its_folders(session, library, peer, make_sync, run_context):
    peer.add("Vacation 2026/Day 1/a.jpg", color=(200, 10, 10))
    peer.add("Vacation 2026/b.jpg", color=(10, 200, 10))
    sync = make_sync()

    result = run_sync(session, sync.id, run_context)

    assert result.outcome == codes.OUTCOME_SYNC_IMPORTED
    assert result.params["imported"] == 2 and result.params["path"] == "lan"
    assert imported(library) == ["Day 1/a.jpg", "b.jpg"]
    rows = {row.item_key: row for row in sync_repository.rows(session, sync.id)}
    assert set(rows) == {"md1:Vacation 2026/Day 1/a.jpg", "md1:Vacation 2026/b.jpg"}
    assert {row.status for row in rows.values()} == {SYNC_ITEM_IMPORTED}
    assert peer.session_args == (DEVICE["id"], f"sync-{sync.id}", "never", None)
    assert peer.closed == [("s_1", f"sync-{sync.id}")]


def test_peer_import_reports_progress_after_each_page(session, library, peer, make_sync, run_context):
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        peer.add(f"Vacation 2026/{name}")
    peer.page_size = 2
    progress = []
    run_context.on_progress = lambda done, total: progress.append((done, total))
    sync = make_sync()

    run_sync(session, sync.id, run_context)

    assert progress == [(2, 3), (3, 3)]


def test_downloads_go_to_one_stable_temp_name_per_record(session, library, peer, make_sync, run_context):
    peer.add("Vacation 2026/a.jpg")
    sync = make_sync()

    run_sync(session, sync.id, run_context)

    expected = hashlib.sha1(b"md1:Vacation 2026/a.jpg").hexdigest()
    destination = Path(peer.started[0][1])
    assert destination.name == f"{TEMP_PREFIX}{sync.id}-{expected}{TEMP_SUFFIX}"
    assert destination.parent == library / "Imports" / "Mom"


def test_a_run_with_nothing_new_never_connects(session, library, peer, make_sync, run_context):
    peer.add("Vacation 2026/a.jpg")
    sync = make_sync()
    run_sync(session, sync.id, run_context)
    peer.add("Vacation 2026/c.jpg", color=(10, 10, 200))

    second = run_sync(session, sync.id, run_context)
    third = run_sync(session, sync.id, run_context)

    assert second.params["imported"] == 1 and second.params["unchanged"] == 1
    assert third.params["imported"] == 0 and "path" not in third.params
    assert peer.opens == 2


def test_two_thousand_photo_scope_across_restart_only_fetches_new_files(
    session, library, peer, make_sync, run_context,
):
    def add_batch(start: int, stop: int) -> None:
        for index in range(start, stop):
            relative_path = f"Vacation 2026/{index:04d}.jpg"
            content = b"photo " + str(index).encode()
            peer.files[relative_path] = {
                "media_item_id": index + 1, "media_dir_id": "md1", "relative_path": relative_path,
                "name": f"{index:04d}.jpg", "size": len(content), "mtime": 1720000000.0,
                "sha1": hashlib.sha1(content).hexdigest(), "source_id": f"md1:{relative_path}",
                "content": content,
            }

    add_batch(0, 1000)
    sync = make_sync()
    first = run_sync(session, sync.id, run_context)
    assert first.params["imported"] == 1000 and first.problem is None
    assert len(peer.started) == 1000

    add_batch(1000, 2000)
    with Session(session.bind) as restarted:
        second = run_sync(restarted, sync.id, run_context)
        assert second.params["imported"] == 1000 and second.params["unchanged"] == 1000
        assert second.problem is None
        assert restarted.query(SyncItem).filter_by(automation_id=sync.id).count() == 2000
        third = run_sync(restarted, sync.id, run_context)

    assert len(peer.started) == 2000
    assert third.params["imported"] == 0 and third.params["unchanged"] == 2000
    assert len(list((library / "Imports" / "Mom").glob("*.jpg"))) == 2000


def test_a_photo_already_in_the_library_is_linked_without_a_download(session, library, peer, make_sync,
                                                                     run_context, add_photo):
    mine = add_photo("Family/same.jpg", color=(1, 2, 3))
    mine.sha1 = file_sha1(Path(mine.full_file_path))
    session.commit()
    peer.add("Vacation 2026/same.jpg", color=(1, 2, 3))
    sync = make_sync()

    result = run_sync(session, sync.id, run_context)

    assert result.params["linked"] == 1
    assert peer.started == [] and peer.opens == 0
    assert sync_repository.rows(session, sync.id)[0].status == SYNC_ITEM_LINKED_EXISTING


def test_downloads_run_ahead_of_the_engine(session, library, peer, make_sync, run_context):
    for n in range(5):
        peer.add(f"Vacation 2026/{n}.jpg", color=(50 * n, 200 - 40 * n, 10))
    sync = make_sync()

    run_sync(session, sync.id, run_context)

    started = [path for path, _ in peer.started]
    assert sorted(started) == sorted(peer.files)
    # When the engine first waited on a download, the next two were already going.
    assert started.index("Vacation 2026/1.jpg") < peer.polled.index("Vacation 2026/0.jpg") + 3
    assert started[:3] == ["Vacation 2026/0.jpg", "Vacation 2026/1.jpg", "Vacation 2026/2.jpg"]
    assert len(started) == len(set(started))  # nothing started twice


def test_read_ahead_skips_records_that_wont_be_downloaded(session, library, peer, make_sync, run_context,
                                                         add_photo):
    peer.add("Vacation 2026/a.jpg", color=(200, 10, 10))
    peer.add("Vacation 2026/b.jpg", color=(10, 200, 10))
    peer.add("Vacation 2026/c.jpg", color=(10, 10, 200))
    mine = add_photo("Family/b.jpg", color=(10, 200, 10))
    mine.sha1 = file_sha1(Path(mine.full_file_path))
    session.commit()
    sync = make_sync()

    run_sync(session, sync.id, run_context)

    assert [path for path, _ in peer.started] == ["Vacation 2026/a.jpg", "Vacation 2026/c.jpg"]


def test_old_relay_setting_cannot_enable_relay(session, library, peer, run_context):
    peer.add("Vacation 2026/a.jpg")
    sync = setup.create_sync(session, "From Mom", 3)
    setup.set_import_root(session, sync, MEDIA_DIR_ID, "Imports/Mom")
    setup.set_options(session, sync, {"device": DEVICE, "scope": SCOPE})
    config = sync_repository.load_sync(sync).config
    sync_repository.save_config(sync, replace(config, options={**config.options,
                                                             "allow_relay": True, "relay_budget_gb": 2}))
    session.commit()
    setup.turn_on(session, sync)

    run_sync(session, sync.id, run_context)

    assert peer.session_args[2:] == ("never", None)


def test_an_unpaired_device_pauses_the_sync(session, library, peer, make_sync, run_context):
    peer.add("Vacation 2026/a.jpg")
    sync = make_sync()
    peer.device["trust_state"] = "revoked"

    result = run_sync(session, sync.id, run_context)

    assert result.problem == codes.PROBLEM_SYNC_PAUSED
    runtime = sync_repository.load_sync(sync).runtime
    assert (runtime.status, runtime.pause_code) == (RUNTIME_PAUSED, PAUSE_DEVICE_REVOKED)


def test_an_offline_device_ends_the_run_quietly(session, library, peer, make_sync, run_context):
    peer.add("Vacation 2026/a.jpg")
    sync = make_sync()
    peer.device["online"] = False

    result = run_sync(session, sync.id, run_context)

    assert result.outcome == codes.OUTCOME_SYNC_WAITING_FOR_DEVICE and result.problem is None
    assert peer.started == []


def test_a_stopped_run_names_the_device_by_its_current_name(session, library, peer, make_sync, run_context):
    peer.add("Vacation 2026/a.jpg")
    sync = make_sync()
    peer.device["display_name"] = "Mom's new laptop"
    peer.fail_files = "peer_offline"

    result = run_sync(session, sync.id, run_context)

    assert result.outcome == codes.OUTCOME_SYNC_WAITING_FOR_DEVICE
    assert result.params["device"] == "Mom's new laptop"


def test_sharing_turned_off(session, library, peer, make_sync, run_context):
    sync = make_sync()
    peer.available = False
    assert run_sync(session, sync.id, run_context).outcome == codes.OUTCOME_SYNC_SHARING_OFF


def test_a_scope_no_longer_shared_pauses_for_a_new_choice(session, library, peer, make_sync, run_context):
    sync = make_sync()
    peer.fail_files = "scope_not_shared"

    run_sync(session, sync.id, run_context)

    assert sync_repository.load_sync(sync).runtime.pause_code == PAUSE_OPTION_MISSING


def test_a_file_that_left_the_share_is_skipped(session, library, peer, make_sync, run_context):
    peer.add("Vacation 2026/a.jpg", color=(200, 10, 10))
    peer.add("Vacation 2026/b.jpg", color=(10, 200, 10))
    peer.refuse.add("Vacation 2026/a.jpg")
    sync = make_sync()

    result = run_sync(session, sync.id, run_context)

    assert result.params["imported"] == 1 and result.problem is None
    skipped = session.query(SyncItem).filter_by(automation_id=sync.id, status=SYNC_ITEM_SKIPPED).one()
    assert skipped.item_key == "md1:Vacation 2026/a.jpg" and skipped.error_code == "file_refused"


def test_without_a_direct_path_the_run_waits(session, library, peer, make_sync, run_context):
    peer.add("Vacation 2026/a.jpg")
    peer.fail_open = "relay_not_allowed"
    sync = make_sync()

    result = run_sync(session, sync.id, run_context)

    assert result.problem is None
    assert result.outcome == codes.OUTCOME_SYNC_WAITING_FOR_DIRECT
    assert imported(library) == []
    assert sync_repository.load_sync(sync).runtime.cursor(CURSOR_LIST).next_cursor is None
    assert peer.session_args[2:] == ("never", None)

    peer.fail_open = None
    again = run_sync(session, sync.id, run_context)
    assert again.params["imported"] == 1


def test_a_device_that_unpairs_mid_run_pauses_after_keeping_what_arrived(session, library, peer, make_sync,
                                                                        run_context):
    peer.add("Vacation 2026/a.jpg", color=(200, 10, 10))
    peer.add("Vacation 2026/b.jpg", color=(10, 200, 10))
    peer.fail_pull["Vacation 2026/b.jpg"] = "not_trusted"
    sync = make_sync()

    result = run_sync(session, sync.id, run_context)

    assert result.problem == codes.PROBLEM_SYNC_PAUSED and result.params["imported"] == 1
    assert sync_repository.load_sync(sync).runtime.pause_code == PAUSE_DEVICE_REVOKED


def test_a_file_that_kept_changing_is_tried_again_next_run(session, library, peer, make_sync, run_context):
    peer.add("Vacation 2026/a.jpg")
    peer.fail_pull["Vacation 2026/a.jpg"] = "source_changed"
    sync = make_sync()

    first = run_sync(session, sync.id, run_context)
    peer.fail_pull.clear()
    second = run_sync(session, sync.id, run_context)

    assert first.problem == codes.PROBLEM_SYNC_ITEMS_FAILED
    assert second.params["imported"] == 1


def test_repeated_web_restarts_end_the_run_without_recursing(session, library, peer, make_sync, run_context,
                                                            monkeypatch):
    peer.add("Vacation 2026/a.jpg")
    sync = make_sync()

    def expired_pull(pull_id, wait=0):
        raise PeerClientError("session_expired", "the web process restarted")

    monkeypatch.setattr(peer, "pull", expired_pull)
    result = run_sync(session, sync.id, run_context)

    assert result.outcome == codes.OUTCOME_SYNC_WAITING_FOR_DEVICE
    assert result.problem is None
    assert peer.opens == peer_source.MAX_SESSION_RECOVERIES + 1


# ---- a filtered subset ---------------------------------------------------------------


def test_a_filtered_import_takes_only_the_matching_files(session, library, peer, make_sync, run_context):
    peer.add("Vacation 2026/beach/a.jpg", color=(10, 20, 30))
    peer.add("Vacation 2026/city/b.jpg", color=(40, 50, 60))
    sync = make_sync()
    setup.set_import_filters(session, sync, {"path": "beach"})
    setup.turn_on(session, sync)

    result = run_sync(session, sync.id, run_context)

    assert result.params["imported"] == 1
    assert imported(library) == ["beach/a.jpg"]
    assert peer.filters_seen and all(f == {"path": "beach"} for f in peer.filters_seen)


def test_test_shows_only_the_matching_files(session, library, peer, make_sync):
    peer.add("Vacation 2026/beach/a.jpg", color=(10, 20, 30))
    peer.add("Vacation 2026/city/b.jpg", color=(40, 50, 60))
    sync = make_sync()
    setup.set_import_filters(session, sync, {"path": "beach"})

    result = preview_sync(session, sync)

    assert [r.filename for r in result.records] == ["a.jpg"]


def test_choosing_a_filter_needs_a_new_review(session, library, peer, make_sync):
    sync = make_sync()
    assert sync.enabled

    setup.set_import_filters(session, sync, {"year": 2024})

    assert not sync.enabled
    assert sync_repository.load_sync(sync).config.source_filters == {"year": 2024}


def test_everything_clears_the_filter(session, library, peer, make_sync):
    sync = make_sync()
    setup.set_import_filters(session, sync, {"year": 2024})

    setup.set_import_filters(session, sync, None)

    config = sync_repository.load_sync(sync).config
    assert config.selection is None and config.source_filters is None


def test_another_share_drops_the_filter_and_the_same_one_keeps_it(session, library, peer, make_sync):
    sync = make_sync()
    setup.set_import_filters(session, sync, {"people": [3]})

    setup.set_options(session, sync, {"device": DEVICE, "scope": SCOPE})
    assert sync_repository.load_sync(sync).config.source_filters == {"people": [3]}

    setup.set_options(session, sync, {"device": DEVICE, "scope": {"id": "album:12", "label": "For Mom"}})
    assert sync_repository.load_sync(sync).config.source_filters is None


# ---- setup ---------------------------------------------------------------------------


def test_choosing_another_device_clears_the_scope(session, library, peer, make_sync):
    sync = make_sync()
    other = {"id": "DAD0-0000-0000-0002", "label": "Dad's PC"}

    setup.set_options(session, sync, {"device": other, "scope": SCOPE})

    options = sync_repository.load_sync(sync).config.options
    assert options["device"] == other and options["scope"] is None
    assert not sync.enabled  # the consent covered the old device


def test_a_peer_sync_owns_a_device_online_trigger_for_its_device(session, library, peer, make_sync):
    sync = make_sync()

    def device_triggers():
        return [t.config["device_id"] for t in session.query(AutomationTrigger)
                .filter_by(automation_id=sync.id, event_type=EVENT_DEVICE_ONLINE)]

    assert device_triggers() == [DEVICE["id"]]
    setup.set_options(session, sync, {"device": {"id": "DAD0-0000-0000-0002", "label": "Dad's PC"}})
    assert device_triggers() == ["DAD0-0000-0000-0002"]


def test_the_device_can_be_saved_before_the_scope_is_picked(session, library, peer):
    """The picker saves the device on its own first; the scope is needed only to turn it on."""
    automation = setup.create_sync(session, "From Mom", 3)
    setup.set_import_root(session, automation, MEDIA_DIR_ID, "Imports/Mom")

    setup.set_options(session, automation, {"device": DEVICE})

    assert setup.missing_setup(session, automation) == ["option:scope"]


def test_records_carry_the_hash_date_and_folder_inside_the_scope():
    manifest = {"media_item_id": 9, "media_dir_id": "md1", "relative_path": "Vacation 2026/Day 1/a.jpg",
                "name": "a.jpg", "size": 10, "mtime": 1.5, "sha1": "ab" * 20, "date_taken": "2024-07-04T18:22:10",
                "source_id": "md1:Vacation 2026/Day 1/a.jpg"}

    record = peer_source.record_of(manifest, "folder:md1:Vacation 2026")

    assert record == {
        "source_id": "md1:Vacation 2026/Day 1/a.jpg", "filename": "a.jpg", "folder": "Day 1", "size": 10,
        "updated_at": "10:1.5", "peer": {"media_item_id": 9, "mtime": 1.5},
        "hash": {"algo": "sha1", "value": "ab" * 20}, "taken_at": {"local": "2024-07-04T18:22:10"},
    }
    assert peer_source.record_of({**manifest, "sha1": None}, "album:3")["folder"] == "Vacation 2026/Day 1"


def test_running_out_of_time_mid_download_keeps_the_partial_and_continues_soon(session, library, peer, make_sync,
                                                                              run_context, monkeypatch):
    from yaffo.sync import source_run

    peer.add("Vacation 2026/a.jpg")
    sync = make_sync()
    continued = []
    monkeypatch.setattr(source_run, "schedule_continuation",
                        lambda session_, automation, seconds: continued.append(seconds))
    monkeypatch.setattr(peer, "pull", lambda pull_id, wait=0: {"state": "active"})
    polls = {"n": 0}

    def out_of_time(self):
        polls["n"] += 1
        return polls["n"] > 3  # the listing loop's checks pass; the download's don't

    monkeypatch.setattr(source_run.SourceRun, "out_of_time", out_of_time)

    result = run_sync(session, sync.id, run_context)

    assert result.problem is None and result.params["imported"] == 0
    assert peer.cancelled == ["p_1"]
    assert continued == [source_run.CONTINUE_AFTER_SECONDS]
