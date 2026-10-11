"""The source half of the sync engine, through the built-in Folder source: "copy new
photos from this SD card into Imports/Camera" end to end."""
import shutil
from pathlib import Path

import pytest

from yaffo.db.models import Job, MediaItem, JOB_STATUS_CANCELLED, JOB_STATUS_RUNNING, MEDIA_STATUS_IMPORTED, \
    MEDIA_STATUS_INDEXED, SYNC_ITEM_IMPORTED, SYNC_ITEM_LINKED_EXISTING, EVENT_MEDIA_MODIFIED
from yaffo.db.repositories import sync_repository
from yaffo.sync import folder_source, setup
from yaffo.sync.engine import run_sync
from yaffo.sync.files import file_sha1
from yaffo.sync.schemas import PAUSE_DESTINATION_MISSING
from yaffo.utils import job_codes as codes

from .conftest import MEDIA_DIR_ID, write_jpeg

pytestmark = pytest.mark.unit


@pytest.fixture
def card(tmp_path) -> Path:
    dcim = tmp_path / "EOS_DIGITAL" / "DCIM"
    write_jpeg(dcim / "100CANON" / "IMG_0001.JPG", "2024:07:04 18:22:10", color=(10, 20, 30))
    write_jpeg(dcim / "100CANON" / "IMG_0002.JPG", "2023:12:25 09:00:00", color=(40, 50, 60))
    (dcim / "100CANON" / "notes.txt").write_text("not a photo")
    (dcim / ".Trashes").mkdir()
    write_jpeg(dcim / ".Trashes" / "old.jpg", color=(1, 1, 1))
    return dcim


@pytest.fixture
def make_source(session, library, card):
    """A Folder source. These tests check dated placement, so it arranges by year and
    month; the layouts have their own tests (test_import_layouts.py)."""
    def make(layout: str = "year_month", **options):
        automation = setup.create_sync(session, "Camera card", adapter_id=1)
        setup.set_options(session, automation, {"folder": str(card), **options})
        setup.set_import_root(session, automation, MEDIA_DIR_ID, "Imports/Camera", layout)
        setup.turn_on(session, automation)
        return automation
    return make


def library_files(library: Path) -> list[str]:
    return sorted(p.relative_to(library).as_posix() for p in library.rglob("*") if p.is_file())


def test_new_photos_are_copied_into_the_import_root_by_capture_month(session, library, card, make_source,
                                                                     run_context, no_watcher_store):
    source = make_source()

    result = run_sync(session, source.id, run_context)

    assert result.outcome == codes.OUTCOME_SYNC_IMPORTED and result.problem is None
    assert result.params["imported"] == 2
    assert library_files(library) == ["Imports/Camera/2023/12/IMG_0002.JPG", "Imports/Camera/2024/07/IMG_0001.JPG"]
    media = session.query(MediaItem).order_by(MediaItem.full_file_path).all()
    assert [(Path(m.full_file_path).name, m.status) for m in media] == [
        ("IMG_0002.JPG", MEDIA_STATUS_IMPORTED), ("IMG_0001.JPG", MEDIA_STATUS_IMPORTED)]
    assert media[0].sha1 == file_sha1(card / "100CANON" / "IMG_0002.JPG")
    # Indexing is queued for exactly the new files, and the watcher is told to ignore them.
    assert sorted(run_context.indexed[0]) == sorted(m.full_file_path for m in media)
    assert sorted(no_watcher_store) == sorted(m.full_file_path for m in media)
    rows = sync_repository.rows(session, source.id)
    assert {(r.item_key, r.status) for r in rows} == {
        ("100CANON/IMG_0001.JPG", SYNC_ITEM_IMPORTED), ("100CANON/IMG_0002.JPG", SYNC_ITEM_IMPORTED)}
    # The card is untouched.
    assert sorted(p.name for p in (card / "100CANON").iterdir()) == ["IMG_0001.JPG", "IMG_0002.JPG", "notes.txt"]


def test_a_second_download_does_not_block_cancel_writes(session, make_source, run_context, monkeypatch):
    source = make_source()
    session.add(Job(id="cancel-me", name=source.slug, status=JOB_STATUS_RUNNING, automation_id=source.id))
    session.commit()
    fetch = folder_source.FolderSource.fetch
    calls = 0

    def check_writer(self, record, destination, ctx):
        nonlocal calls
        calls += 1
        if calls == 2:
            # The first import has flushed a media row. A Cancel request uses a
            # separate connection to write its Job status at this point.
            with session.get_bind().begin() as connection:
                connection.exec_driver_sql("PRAGMA busy_timeout=50")
                connection.exec_driver_sql(
                    "UPDATE jobs SET status='CANCELLED' WHERE id='cancel-me'"
                )
        return fetch(self, record, destination, ctx)

    monkeypatch.setattr(folder_source.FolderSource, "fetch", check_writer)

    result = run_sync(session, source.id, run_context)

    assert result.params["imported"] == 2
    assert calls == 2
    session.expire_all()
    assert session.get(Job, "cancel-me").status == JOB_STATUS_CANCELLED


def test_a_crash_after_one_committed_file_requeues_its_indexing(session, library, card, make_source,
                                                                run_context, monkeypatch):
    source = make_source()
    fetch = folder_source.FolderSource.fetch
    calls = 0

    def crash_on_second(self, record, destination, ctx):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("worker stopped")
        return fetch(self, record, destination, ctx)

    monkeypatch.setattr(folder_source.FolderSource, "fetch", crash_on_second)
    with pytest.raises(RuntimeError, match="worker stopped"):
        run_sync(session, source.id, run_context)
    session.rollback()
    assert session.query(MediaItem).count() == 1
    assert run_context.indexed == []

    monkeypatch.setattr(folder_source.FolderSource, "fetch", fetch)
    result = run_sync(session, source.id, run_context)

    assert result.params["imported"] == 1
    assert sorted(run_context.indexed[0]) == sorted([
        str(library / "Imports/Camera/2024/07/IMG_0001.JPG"),
        str(library / "Imports/Camera/2023/12/IMG_0002.JPG"),
    ])


def test_a_second_run_copies_only_what_is_new(session, library, card, make_source, run_context):
    source = make_source()
    run_sync(session, source.id, run_context)
    write_jpeg(card / "100CANON" / "IMG_0003.JPG", "2024:08:01 10:00:00", color=(70, 80, 90))

    result = run_sync(session, source.id, run_context)

    assert (result.params["imported"], result.params["unchanged"]) == (1, 2)
    assert "Imports/Camera/2024/08/IMG_0003.JPG" in library_files(library)
    assert run_context.indexed[-1] == [str(library / "Imports/Camera/2024/08/IMG_0003.JPG")]


def test_photos_already_in_the_library_are_linked_not_copied(session, library, card, make_source, run_context):
    existing = library / "Family" / "same.jpg"
    existing.parent.mkdir()
    shutil.copyfile(card / "100CANON" / "IMG_0001.JPG", existing)
    media = MediaItem(full_file_path=str(existing), sha1=file_sha1(existing))
    session.add(media)
    session.commit()
    source = make_source()

    result = run_sync(session, source.id, run_context)

    assert (result.params["imported"], result.params["linked"]) == (1, 1)
    linked = sync_repository.rows_by_key(session, source.id, ["100CANON/IMG_0001.JPG"])["100CANON/IMG_0001.JPG"]
    assert (linked.status, linked.media_item_id) == (SYNC_ITEM_LINKED_EXISTING, media.id)
    assert "Imports/Camera/2024/07/IMG_0001.JPG" not in library_files(library)


def test_two_identical_files_on_the_card_make_one_photo(session, library, card, make_source, run_context):
    shutil.copyfile(card / "100CANON" / "IMG_0001.JPG", card / "100CANON" / "IMG_0001 copy.JPG")
    source = make_source()

    result = run_sync(session, source.id, run_context)

    assert (result.params["imported"], result.params["linked"]) == (2, 1)
    assert session.query(MediaItem).count() == 2


def test_videos_only_and_no_subfolders(session, library, card, make_source, run_context):
    (card / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42video")
    source = make_source(media="videos", include_subfolders=False)

    result = run_sync(session, source.id, run_context)

    assert result.params["imported"] == 1
    assert [Path(p).name for p in library_files(library)] == ["clip.mp4"]


def test_an_edited_file_replaces_the_library_copy(session, library, card, make_source, run_context, trash,
                                                  no_events):
    source = make_source()
    run_sync(session, source.id, run_context)
    copy = library / "Imports/Camera/2024/07/IMG_0001.JPG"
    media = session.query(MediaItem).filter_by(full_file_path=str(copy)).one()
    original_bytes = copy.read_bytes()
    write_jpeg(card / "100CANON" / "IMG_0001.JPG", "2024:07:04 18:22:10", color=(250, 250, 0))

    result = run_sync(session, source.id, run_context)

    assert result.params["replaced"] == 1 and result.params["imported"] == 0
    assert copy.read_bytes() == (card / "100CANON" / "IMG_0001.JPG").read_bytes()
    assert [p.read_bytes() for p in trash.iterdir()] == [original_bytes]
    session.refresh(media)
    assert media.sha1 == file_sha1(copy)
    assert run_context.reindexed == [[media.id]]
    assert no_events == [(EVENT_MEDIA_MODIFIED, {"media_item_ids": [media.id]})]


def test_a_touched_but_unchanged_file_is_not_copied_again(session, library, card, make_source, run_context, trash):
    source = make_source()
    run_sync(session, source.id, run_context)
    photo = card / "100CANON" / "IMG_0001.JPG"
    photo.touch()

    result = run_sync(session, source.id, run_context)

    assert (result.params["replaced"], result.params["imported"]) == (0, 0)
    assert list(trash.iterdir()) == []


def test_a_missing_card_is_waited_for_not_an_error(session, library, card, make_source, run_context):
    source = make_source()
    card.rename(card.with_name("unplugged"))

    result = run_sync(session, source.id, run_context)

    assert result.outcome == codes.OUTCOME_SYNC_WAITING_FOR_FOLDER and result.problem is None


def test_an_interrupted_backfill_resumes_from_its_cursor(session, library, card, make_source, run_context,
                                                         monkeypatch):
    for n in range(3, 8):
        write_jpeg(card / "100CANON" / f"IMG_000{n}.JPG", f"2024:01:0{n} 10:00:00", color=(n, n, n))
    monkeypatch.setattr(folder_source, "PAGE_SIZE", 3)
    source = make_source()
    pages = []
    original = folder_source.FolderSource.list_page

    def one_page_then_stop(self, cursor, ctx):
        pages.append(cursor)
        run_context.time_budget_seconds = -1
        return original(self, cursor, ctx)

    monkeypatch.setattr(folder_source.FolderSource, "list_page", one_page_then_stop)

    first = run_sync(session, source.id, run_context)
    cursor = sync_repository.load_sync(source).runtime.cursor("list")
    assert first.params["imported"] == 3 and cursor.next_cursor == "100CANON/IMG_0003.JPG"
    assert cursor.pass_kind == "backfill" and cursor.last_completed_at is None

    run_context.time_budget_seconds = 60
    monkeypatch.setattr(folder_source.FolderSource, "list_page", original)
    second = run_sync(session, source.id, run_context)

    assert second.params["imported"] == 4
    cursor = sync_repository.load_sync(source).runtime.cursor("list")
    assert cursor.next_cursor is None and cursor.last_completed_at is not None and cursor.pass_kind == "incremental"
    assert session.query(MediaItem).count() == 7


def test_a_removed_media_folder_pauses_the_import(session, library, card, make_source, run_context):
    source = make_source()
    shutil.rmtree(library)

    result = run_sync(session, source.id, run_context)

    assert result.problem_params == {"pause_code": PAUSE_DESTINATION_MISSING}


@pytest.mark.parametrize("inside", ["library", "import_root"])
def test_a_source_folder_overlapping_the_library_is_refused(session, library, run_context, inside):
    folder = library / "Family" if inside == "library" else library / "Imports" / "Camera" / "2024"
    write_jpeg(folder / "x.jpg")
    source = setup.create_sync(session, "Loop", adapter_id=1)
    setup.set_options(session, source, {"folder": str(folder)})
    setup.set_import_root(session, source, MEDIA_DIR_ID, "Imports/Camera", "year_month")
    setup.turn_on(session, source)

    result = run_sync(session, source.id, run_context)

    assert result.problem == codes.PROBLEM_SYNC_INVALID_CONFIG
    assert session.query(MediaItem).count() == 0


def test_library_photos_indexed_before_hashes_existed_are_still_recognised(session, library, card, make_source,
                                                                         run_context):
    existing = library / "Family" / "old.jpg"
    existing.parent.mkdir()
    shutil.copyfile(card / "100CANON" / "IMG_0002.JPG", existing)
    media = MediaItem(full_file_path=str(existing), status=MEDIA_STATUS_INDEXED, sha1=None)
    session.add(media)
    session.commit()

    result = run_sync(session, make_source().id, run_context)

    assert (result.params["imported"], result.params["linked"]) == (1, 1)
    session.refresh(media)
    assert media.sha1 == file_sha1(existing)


def test_a_crashed_imports_temporary_file_is_swept(session, library, card, make_source, run_context):
    source = make_source()
    root = library / "Imports" / "Camera"
    root.mkdir(parents=True)
    leftover = root / f".yaffo-{source.id}-dead.part"
    leftover.write_bytes(b"half a photo")

    run_sync(session, source.id, run_context)

    assert not leftover.exists()
    assert not list(library.rglob("*.part"))
