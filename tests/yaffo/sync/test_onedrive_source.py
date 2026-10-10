"""The OneDrive source preset through the real engine, Starlark hooks, and web layer,
against the fake Graph: backfill across runs, crashes, incremental passes, deletions,
edits, throttling, sign-in loss, and the Phase 2 exit criterion."""
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from yaffo.db.models import (
    AutomationTrigger, MediaItem, SyncAdapter, SyncItem, SYNC_ITEM_DELETED_AT_SOURCE, SYNC_ITEM_FAILED,
    SYNC_ITEM_IMPORTED, TRIGGER_TYPE_SCHEDULE,
)
from yaffo.db.repositories import sync_repository
from yaffo.sync import connections, preset_seed, source_run
from yaffo.sync.engine import run_sync
from yaffo.sync.handlers import ERROR_RATE_LIMITED
from yaffo.sync.schemas import (
    CONNECTION_NEEDS_SIGN_IN, CURSOR_LIST, PAUSE_OPTION_MISSING, RUNTIME_NEEDS_SIGN_IN, RUNTIME_PAUSED,
)
from yaffo.sync.web import url_hash
from yaffo.utils import job_codes as codes
from yaffo.utils.time import utcnow


def imported_files(library: Path) -> list[Path]:
    return sorted(p for p in (library / "Imports" / "OneDrive").rglob("*") if p.is_file())


def run(session, automation, run_context):
    return run_sync(session, automation.id, run_context)


def limit_pages(monkeypatch, pages: int) -> None:
    """End each run after `pages` pages, as the time budget would."""
    original = source_run.SourceRun.execute

    def execute(self):
        calls = {"n": 0}

        def out_of_time():
            calls["n"] += 1
            return calls["n"] > pages
        self.out_of_time = out_of_time
        return original(self)
    monkeypatch.setattr(source_run.SourceRun, "execute", execute)


def test_backfill_imports_photos_and_skips_other_files(session, library, onedrive_sync, fake_onedrive, run_context):
    fake_onedrive.state.add_photos(25)
    automation = onedrive_sync()
    result = run(session, automation, run_context)
    assert result.outcome == codes.OUTCOME_SYNC_IMPORTED and result.problem is None
    assert result.params["imported"] == 25
    files = imported_files(library)
    assert len(files) == 25 and not any(f.suffix == ".txt" for f in files)
    # Photos in subfolders of the chosen folder come too.
    assert any(item.parent == "CAMROLL-2024" for item in fake_onedrive.state.items.values() if not item.folder)
    assert sync_repository.status_counts(session, automation.id) == {SYNC_ITEM_IMPORTED: 25}
    # The provider's location fills in for files without GPS of their own.
    located = session.query(MediaItem).filter(MediaItem.latitude.isnot(None)).count()
    assert located == 5
    assert sum(len(batch) for batch in run_context.indexed) == 25
    assert fake_onedrive.state.counters.get("download_with_credentials", 0) == 0
    runtime = sync_repository.load_sync(automation).runtime
    cursor = runtime.cursor(CURSOR_LIST)
    assert cursor.next_cursor is None and "token=" in cursor.checkpoint
    assert url_hash(cursor.checkpoint) in runtime.followups


def test_incremental_pass_imports_only_changes(session, library, onedrive_sync, fake_onedrive, run_context, trash):
    ids = fake_onedrive.state.add_photos(12)
    automation = onedrive_sync()
    run(session, automation, run_context)
    downloads = fake_onedrive.state.counters["downloads"]

    fake_onedrive.state.add_photos(3)
    with fake_onedrive.state.lock:
        item = fake_onedrive.state.items[ids[0]]
        item.deleted, item.seq = True, fake_onedrive.state.next_seq()
    result = run(session, automation, run_context)
    assert result.params["imported"] == 3 and result.params["deleted_at_source"] == 1
    assert fake_onedrive.state.counters["downloads"] == downloads + 3
    # Deleted in OneDrive: marked, never deleted locally.
    assert len(imported_files(library)) == 15
    row = session.query(SyncItem).filter_by(automation_id=automation.id, item_key=ids[0]).one()
    assert row.status == SYNC_ITEM_DELETED_AT_SOURCE and row.media_item_id is not None

    # An edit at the source replaces the library's copy in place.
    original = session.get(MediaItem, session.query(SyncItem).filter_by(item_key=ids[1]).one().media_item_id)
    before = Path(original.full_file_path).read_bytes()
    with fake_onedrive.state.lock:
        edited = fake_onedrive.state.items[ids[1]]
        edited.content = edited.content + b"\x00edited"
        edited.modified, edited.seq = "2025-06-01T00:00:00Z", fake_onedrive.state.next_seq()
    result = run(session, automation, run_context)
    assert result.params["replaced"] == 1
    assert Path(original.full_file_path).read_bytes() != before
    assert len(list(trash.iterdir())) == 1


def test_backfill_resumes_across_runs_and_restarts(session, library, onedrive_sync, fake_onedrive, run_context,
                                                   monkeypatch):
    fake_onedrive.state.add_photos(40)  # 4 folders + 40 photos + 1 note, 10 per page
    automation = onedrive_sync()
    limit_pages(monkeypatch, 2)
    first = run(session, automation, run_context)
    cursor = sync_repository.load_sync(automation).runtime.cursor(CURSOR_LIST)
    assert cursor.next_cursor is not None and cursor.checkpoint is None
    assert 0 < first.params["imported"] < 40
    # The run scheduled its own continuation a minute out.
    trigger = session.query(AutomationTrigger).filter_by(automation_id=automation.id,
                                                         trigger_type=TRIGGER_TYPE_SCHEDULE).one()
    assert trigger.next_run_at <= utcnow() + timedelta(seconds=90)

    # A restart: a fresh session (and fresh handler objects) picks up the stored cursor.
    total = first.params["imported"]
    for _ in range(5):
        fresh = Session(bind=session.get_bind())
        result = run_sync(fresh, automation.id, run_context)
        total += result.params["imported"]
        fresh.close()
        if sync_repository.load_sync(session.get(type(automation), automation.id)).runtime.cursor(
                CURSOR_LIST).next_cursor is None:
            break
        session.expire_all()
    assert total == 40 and len(imported_files(library)) == 40


def test_a_crash_mid_page_never_duplicates(session, library, onedrive_sync, fake_onedrive, run_context, monkeypatch):
    fake_onedrive.state.add_photos(20)
    automation = onedrive_sync()
    original = source_run.SourceRun.process
    seen = {"n": 0}

    def crashing(self, record, known, ctx, seen_hashes):
        seen["n"] += 1
        if seen["n"] == 13:
            raise RuntimeError("worker killed")
        return original(self, record, known, ctx, seen_hashes)
    monkeypatch.setattr(source_run.SourceRun, "process", crashing)
    with pytest.raises(RuntimeError):
        run(session, automation, run_context)
    session.rollback()
    monkeypatch.setattr(source_run.SourceRun, "process", original)
    fresh = Session(bind=session.get_bind())
    result = run_sync(fresh, automation.id, run_context)
    assert result.problem is None
    assert fresh.query(MediaItem).count() == 20
    assert len(imported_files(library)) == 20
    assert not any(" (2)" in f.name for f in imported_files(library))
    fresh.close()


def test_failed_downloads_are_retried_by_later_runs(session, library, onedrive_sync, fake_onedrive, run_context):
    ids = fake_onedrive.state.add_photos(5)
    fake_onedrive.state.broken_downloads.add(ids[2])
    automation = onedrive_sync()
    result = run(session, automation, run_context)
    assert result.params["imported"] == 4 and result.problem == codes.PROBLEM_SYNC_ITEMS_FAILED
    row = session.query(SyncItem).filter_by(item_key=ids[2]).one()
    assert row.status == SYNC_ITEM_FAILED and row.details["record"]["source_id"] == ids[2]

    fake_onedrive.state.broken_downloads.clear()
    result = run(session, automation, run_context)  # the delta feed has nothing new
    assert result.params["imported"] == 1 and result.problem is None
    session.refresh(row)
    assert row.status == SYNC_ITEM_IMPORTED and "record" not in row.details


def test_throttling_stops_the_run_and_schedules_the_next(session, library, onedrive_sync, fake_onedrive,
                                                          run_context):
    fake_onedrive.state.add_photos(5)
    automation = onedrive_sync()
    fake_onedrive.state.throttle = 1
    result = run(session, automation, run_context)
    assert result.problem == codes.PROBLEM_SYNC_STOPPED and result.problem_params == {"code": ERROR_RATE_LIMITED}
    assert sync_repository.load_sync(automation).runtime.cursor(CURSOR_LIST).last_completed_at is None
    trigger = session.query(AutomationTrigger).filter_by(automation_id=automation.id,
                                                         trigger_type=TRIGGER_TYPE_SCHEDULE).one()
    assert utcnow() + timedelta(seconds=100) < trigger.next_run_at <= utcnow() + timedelta(seconds=130)
    result = run(session, automation, run_context)
    assert result.params["imported"] == 5


def test_a_revoked_account_stops_every_sync_until_sign_in(session, library, onedrive_sync, fake_onedrive,
                                                           run_context):
    fake_onedrive.state.add_photos(3)
    automation = onedrive_sync()
    fake_onedrive.state.revoked = True
    result = run(session, automation, run_context)
    assert result.problem == codes.PROBLEM_SYNC_NEEDS_SIGN_IN
    session.expire_all()
    assert sync_repository.load_sync(automation).runtime.status == RUNTIME_NEEDS_SIGN_IN
    key = sync_repository.load_sync(automation).config.connection_key
    assert connections.get(session, key).status == CONNECTION_NEEDS_SIGN_IN
    calls = fake_onedrive.state.counters.get("graph_calls", 0)
    result = run(session, automation, run_context)
    assert result.problem == codes.PROBLEM_SYNC_NEEDS_SIGN_IN
    assert fake_onedrive.state.counters.get("graph_calls", 0) == calls  # nothing called

    # Signing in again (same account) fixes it.
    fake_onedrive.state.revoked = False
    connections.upsert_after_sign_in(session, "onedrive", "fake-user-1", "jason@example.com",
                                     ["Files.Read", "User.Read"], fake_onedrive.grant_token())
    result = run(session, automation, run_context)
    assert result.problem is None and result.params["imported"] == 3


def test_a_deleted_folder_pauses_the_sync(session, library, onedrive_sync, fake_onedrive, run_context):
    automation = onedrive_sync(folder="GONE", label="Old folder")
    result = run(session, automation, run_context)
    assert result.problem == codes.PROBLEM_SYNC_PAUSED
    runtime = sync_repository.load_sync(automation).runtime
    assert runtime.status == RUNTIME_PAUSED and runtime.pause_code == PAUSE_OPTION_MISSING


def test_an_expired_checkpoint_restarts_the_pass(session, library, onedrive_sync, fake_onedrive, run_context):
    fake_onedrive.state.add_photos(4)
    automation = onedrive_sync()
    run(session, automation, run_context)
    column = sync_repository.load_sync(automation)
    bogus = "https://graph.microsoft.com/v1.0/drives/D1/items/CAMROLL/delta?token=99999"
    cursor = column.runtime.cursor(CURSOR_LIST)
    from dataclasses import replace
    sync_repository.save_runtime(automation, replace(
        column.runtime.with_cursor(CURSOR_LIST, replace(cursor, checkpoint=bogus)), followups=[url_hash(bogus)]))
    session.commit()
    result = run(session, automation, run_context)
    assert result.problem is None and result.params["imported"] == 0 and result.params["unchanged"] == 4


def test_photos_already_in_the_library_are_linked_by_hash(session, library, onedrive_sync, fake_onedrive,
                                                          run_context):
    ids = fake_onedrive.state.add_photos(2)
    content = fake_onedrive.state.items[ids[0]].content
    existing = library / "already.jpg"
    existing.write_bytes(content)
    import hashlib
    session.add(MediaItem(full_file_path=str(existing), sha1=hashlib.sha1(content).hexdigest()))
    session.commit()
    automation = onedrive_sync()
    result = run(session, automation, run_context)
    assert result.params == {**result.params, "imported": 1, "linked": 1}
    assert fake_onedrive.state.counters["downloads"] == 1  # the hash matched before downloading


def test_preset_reseed_keeps_consent_when_the_grants_do_not_change(session, library, onedrive_sync,
                                                                   fake_onedrive, monkeypatch, run_context):
    automation = onedrive_sync()
    row = session.query(SyncAdapter).filter_by(slug="preset.onedrive_source").one()
    row.published_code = row.published_code + "\n# a comment-only fix\n"
    row.preset_version = "0"
    session.commit()
    preset_seed.ensure_presets(session)
    assert row.preset_version == preset_seed.PRESETS[0].version
    result = run(session, automation, run_context)
    assert result.problem is None


@pytest.mark.slow
def test_exit_10000_photo_backfill_across_runs_and_restarts(session, library, onedrive_sync, fake_onedrive,
                                                             run_context, monkeypatch):
    """Phase 2 exit: a 10,000-photo backfill completes across several runs and
    restarts, and later runs import only new photos."""
    # Reading each file's capture date with exiftool dominates a test this size and
    # isn't what's under test; the year/month folders fall back to the record's date.
    monkeypatch.setattr(source_run, "get_exif_data_with_exiftool", lambda path: None)
    fake_onedrive.state.page_size = 200
    fake_onedrive.state.add_photos(10_000)
    automation = onedrive_sync()
    limit_pages(monkeypatch, 15)
    runs = 0
    while True:
        runs += 1
        fresh = Session(bind=session.get_bind())  # every run is a restart
        result = run_sync(fresh, automation.id, run_context)
        assert result.problem is None, result
        done = sync_repository.load_sync(fresh.get(type(automation), automation.id)).runtime.cursor(
            CURSOR_LIST).last_completed_at
        fresh.close()
        if done:
            break
        assert runs < 10
    assert runs >= 4
    session.expire_all()
    assert session.query(MediaItem).count() == 10_000
    assert sync_repository.status_counts(session, automation.id) == {SYNC_ITEM_IMPORTED: 10_000}
    downloads = fake_onedrive.state.counters["downloads"]
    assert downloads == 10_000

    fake_onedrive.state.add_photos(7)
    result = run_sync(session, automation.id, run_context)
    assert result.params["imported"] == 7 and result.params["unchanged"] == 0
    assert fake_onedrive.state.counters["downloads"] == downloads + 7
