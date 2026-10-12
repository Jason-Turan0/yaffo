"""The target half of the sync engine, through the built-in Folder target: "keep my
For Mom album copied to a folder" end to end, including conversion, removals,
deletes from Yaffo, crash recovery, consent and pauses."""
import os
from pathlib import Path

import pytest
from PIL import Image

from yaffo.db.models import AlbumItem, Job, SyncItem, SYNC_ITEM_CANT_REMOVE, SYNC_ITEM_REMOVED, SYNC_ITEM_REMOVED_REMOTELY, \
    SYNC_ITEM_FAILED, SYNC_ITEM_REMOVING, SYNC_ITEM_SENT, SYNC_ITEM_PENDING
from yaffo.db.repositories import sync_repository
from yaffo.sync import folder_target, setup
from yaffo.sync.engine import run_sync
from yaffo.sync.schemas import (
    PAUSE_DESTINATION_MISSING, PAUSE_SELECTION_MISSING, RUNTIME_NEEDS_CONSENT, RUNTIME_PAUSED, RUNTIME_READY,
    Selection,
)
from yaffo.utils import job_codes as codes

pytestmark = pytest.mark.unit

HEIC = Path(__file__).parents[1] / "utils" / "test_data" / "heic" / "mobile" / "iphone_13_pro_max.HEIC"


@pytest.fixture
def destination(tmp_path) -> Path:
    folder = tmp_path / "Amazon Photos Backup"
    folder.mkdir()
    return folder


@pytest.fixture
def make_target(session, destination):
    def make(album_row, **options):
        automation = setup.create_sync(session, "For Mom to folder", adapter_id=2)
        setup.set_options(session, automation, {"folder": str(destination), **options})
        setup.set_selection(session, automation, Selection(kind="album", album_id=album_row.id))
        setup.turn_on(session, automation)
        return automation
    return make


def files_in(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def runtime(automation):
    return sync_repository.load_sync(automation).runtime


def test_album_is_copied_into_a_subfolder_named_after_it(session, add_photo, album, make_target,
                                                          destination, run_context):
    a, b = add_photo("a.jpg"), add_photo("b.jpg", color=(0, 90, 0))
    target = make_target(album("For Mom", [a, b]))

    result = run_sync(session, target.id, run_context)

    assert result.outcome == codes.OUTCOME_SYNC_SENT and result.problem is None
    assert result.params["sent"] == 2
    assert files_in(destination) == ["For Mom/a.jpg", "For Mom/b.jpg"]
    rows = sync_repository.rows(session, target.id)
    assert {(r.item_key, r.status, r.remote_id) for r in rows} == {
        (f"{a.id}:For Mom", SYNC_ITEM_SENT, "For Mom/a.jpg"),
        (f"{b.id}:For Mom", SYNC_ITEM_SENT, "For Mom/b.jpg"),
    }
    copy = destination / "For Mom" / "a.jpg"
    assert rows[0].details == {"size": copy.stat().st_size, "mtime_ns": copy.stat().st_mtime_ns,
                               "folder": str(destination)}
    # The copy's modification time is the capture date (what frames and sync apps sort by).
    assert int(copy.stat().st_mtime) == int(__import__("datetime").datetime(2024, 7, 4, 18, 22, 10).timestamp())
    assert runtime(target).status == RUNTIME_READY


def test_a_second_run_changes_nothing(session, add_photo, album, make_target, destination, run_context):
    target = make_target(album("For Mom", [add_photo("a.jpg")]))
    run_sync(session, target.id, run_context)
    before = {p: (destination / p).stat().st_mtime_ns for p in files_in(destination)}

    result = run_sync(session, target.id, run_context)

    assert result.params["sent"] == 0 and result.params["unchanged"] == 1
    assert {p: (destination / p).stat().st_mtime_ns for p in files_in(destination)} == before


def test_convert_to_jpeg_sends_a_jpeg_copy_and_keeps_the_capture_date(session, library, album, make_target,
                                                                       destination, run_context):
    from yaffo.db.models import MediaItem, MEDIA_STATUS_INDEXED
    heic = library / "IMG_0001.HEIC"
    heic.write_bytes(HEIC.read_bytes())
    png = library / "screen.png"
    Image.new("RGB", (40, 30), (1, 2, 3)).save(png)
    media = [MediaItem(full_file_path=str(heic), status=MEDIA_STATUS_INDEXED, date_taken="2022-02-16T12:55:41"),
             MediaItem(full_file_path=str(png), status=MEDIA_STATUS_INDEXED)]
    session.add_all(media)
    session.commit()
    target = make_target(album("Frame", media), convert="jpeg", layout="flat")

    result = run_sync(session, target.id, run_context)

    assert result.params["sent"] == 2 and result.params["converted"] == 1
    assert files_in(destination) == ["IMG_0001.jpg", "screen.png"]
    with Image.open(destination / "IMG_0001.jpg") as copy:
        assert copy.format == "JPEG"
        assert copy.size == (3024, 4032)  # upright, as libheif decoded it
        exif = copy.getexif()
        assert exif.get(274) == 1
        assert exif.get_ifd(0x8769).get(36867) == "2022:02:16 12:55:41"
    row = sync_repository.rows(session, target.id)[0]
    assert row.details["sent_as"]["type"] == "image/jpeg"
    assert heic.read_bytes() == HEIC.read_bytes()  # the original is untouched


def test_frame_sizing_caps_pixels_and_bytes(session, add_photo, album, make_target, destination, run_context,
                                            monkeypatch):
    monkeypatch.setattr(folder_target, "FRAME_MAX_PIXELS", 1000)
    big = add_photo("big.jpg", size=(400, 300))
    target = make_target(album("Frame", [big]), convert="frame", layout="flat")

    run_sync(session, target.id, run_context)

    with Image.open(destination / "big.jpg") as copy:
        assert copy.width * copy.height <= 1000


def test_videos_are_not_transcoded(session, library, album, make_target, run_context):
    from yaffo.db.models import MediaItem, MEDIA_STATUS_INDEXED, MEDIA_TYPE_VIDEO
    clip = library / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    media = MediaItem(full_file_path=str(clip), status=MEDIA_STATUS_INDEXED, media_type=MEDIA_TYPE_VIDEO)
    session.add(media)
    session.commit()
    target = make_target(album("Frame", [media]), convert="jpeg")

    result = run_sync(session, target.id, run_context)

    assert result.params["unsupported"] == 1 and result.params["sent"] == 0
    assert result.problem == codes.PROBLEM_SYNC_ITEMS_FAILED
    assert sync_repository.rows(session, target.id)[0].error_code == "unsupported_format"


def test_a_photo_removed_from_the_album_is_deleted_from_the_folder(session, add_photo, album, make_target,
                                                                    destination, trash, run_context):
    a, b = add_photo("a.jpg"), add_photo("b.jpg", color=(0, 0, 200))
    mom = album("For Mom", [a, b])
    target = make_target(mom)
    run_sync(session, target.id, run_context)

    session.query(AlbumItem).filter_by(album_id=mom.id, media_item_id=b.id).delete()
    session.commit()
    result = run_sync(session, target.id, run_context)

    assert result.params["removed"] == 1
    assert files_in(destination) == ["For Mom/a.jpg"]
    assert [p.name for p in trash.iterdir()] == ["0-b.jpg"]
    removed = sync_repository.rows(session, target.id, [SYNC_ITEM_REMOVED])
    assert [r.media_item_id for r in removed] == [b.id]

    # Back in the album: copied again.
    session.add(AlbumItem(album_id=mom.id, media_item_id=b.id, position=5))
    session.commit()
    assert run_sync(session, target.id, run_context).params["sent"] == 1
    assert files_in(destination) == ["For Mom/a.jpg", "For Mom/b.jpg"]


def test_keep_leaves_the_copy_when_a_photo_leaves_the_selection(session, add_photo, album, make_target,
                                                                destination, trash, run_context):
    a = add_photo("a.jpg")
    mom = album("For Mom", [a])
    target = make_target(mom, on_leave="keep")
    run_sync(session, target.id, run_context)
    session.query(AlbumItem).delete()
    session.commit()

    result = run_sync(session, target.id, run_context)

    assert result.params["kept"] == 1 and result.params["removed"] == 0
    assert files_in(destination) == ["For Mom/a.jpg"]


def test_a_copy_the_user_changed_is_never_deleted(session, add_photo, album, make_target, destination, trash,
                                                   run_context):
    a = add_photo("a.jpg")
    mom = album("For Mom", [a])
    target = make_target(mom)
    run_sync(session, target.id, run_context)
    (destination / "For Mom" / "a.jpg").write_bytes(b"edited by someone")
    session.query(AlbumItem).delete()
    session.commit()

    result = run_sync(session, target.id, run_context)

    assert result.params["cant_remove"] == 1
    assert (destination / "For Mom" / "a.jpg").read_bytes() == b"edited by someone"
    row = sync_repository.rows(session, target.id)[0]
    assert (row.status, row.error_code) == (SYNC_ITEM_CANT_REMOVE, "changed")


def test_deleting_the_photo_from_yaffo_removes_its_copy(session, add_photo, album, make_target, destination,
                                                       trash, run_context):
    from yaffo.db.repositories import media_repository
    a, b = add_photo("a.jpg"), add_photo("b.jpg", color=(9, 9, 9))
    target = make_target(album("For Mom", [a, b]))
    run_sync(session, target.id, run_context)

    queued = sync_repository.mark_removing_for_deleted_media(session, [a.id])
    media_repository.delete_media_items(session, [a.id])

    assert queued == [target.id]
    row = session.query(SyncItem).filter_by(remote_id="For Mom/a.jpg").one()
    assert (row.status, row.media_item_id) == (SYNC_ITEM_REMOVING, None)

    result = run_sync(session, target.id, run_context)

    assert result.params["removed"] == 1
    assert files_in(destination) == ["For Mom/b.jpg"]
    assert session.query(SyncItem).filter_by(remote_id="For Mom/a.jpg").count() == 0  # nothing left to describe


def test_renaming_the_album_moves_the_copies(session, add_photo, album, make_target, destination, trash,
                                             run_context):
    a = add_photo("a.jpg")
    mom = album("For Mom", [a])
    target = make_target(mom)
    run_sync(session, target.id, run_context)

    mom.name = "Grandma"
    session.commit()
    result = run_sync(session, target.id, run_context)

    assert (result.params["removed"], result.params["sent"]) == (1, 1)
    assert files_in(destination) == ["Grandma/a.jpg"]


class CrashingTarget(folder_target.FolderTarget):
    """Copies the batch, then dies before the engine can record the results."""

    def push(self, batch, ctx):
        super().push(batch, ctx)
        raise SystemExit("worker killed")


def test_a_run_killed_mid_batch_resumes_without_duplicates(session, add_photo, album, make_target, destination,
                                                           run_context, monkeypatch):
    from yaffo.sync import adapters
    target = make_target(album("For Mom", [add_photo("a.jpg"), add_photo("b.jpg", color=(1, 1, 1))]))
    builtin = adapters.BUILTINS["folder_target"]
    monkeypatch.setitem(adapters.BUILTINS, "folder_target",
                        adapters.BuiltinAdapter(builtin.role, builtin.options, CrashingTarget))
    with pytest.raises(SystemExit):
        run_sync(session, target.id, run_context)
    session.rollback()
    assert {r.status for r in sync_repository.rows(session, target.id)} == {SYNC_ITEM_PENDING}
    assert files_in(destination) == ["For Mom/a.jpg", "For Mom/b.jpg"]

    monkeypatch.setitem(adapters.BUILTINS, "folder_target", builtin)
    result = run_sync(session, target.id, run_context)

    assert result.problem is None
    assert files_in(destination) == ["For Mom/a.jpg", "For Mom/b.jpg"]  # no "a (2).jpg"
    assert {r.status for r in sync_repository.rows(session, target.id)} == {SYNC_ITEM_SENT}


class RejectingTarget(folder_target.FolderTarget):
    """Refuses every item the way a service does, with its own reason."""

    def push(self, batch, ctx):
        return {"results": [{"key": item["key"], "error": {"code": "rejected", "message": "fileCreatedAt must be a date"}}
                            for item in batch]}


def test_a_failed_items_message_is_kept_and_logged_until_it_is_sent(session, add_photo, album, make_target,
                                                                     destination, run_context, monkeypatch):
    from yaffo.sync import adapters, run_common
    warnings = []
    monkeypatch.setattr(run_common.logger, "warning", warnings.append)
    target = make_target(album("For Mom", [add_photo("a.jpg")]))
    builtin = adapters.BUILTINS["folder_target"]
    monkeypatch.setitem(adapters.BUILTINS, "folder_target",
                        adapters.BuiltinAdapter(builtin.role, builtin.options, RejectingTarget))

    result = run_sync(session, target.id, run_context)

    assert result.params["failed"] == 1
    row = sync_repository.rows(session, target.id)[0]
    assert (row.status, row.error_code) == (SYNC_ITEM_FAILED, "rejected")
    assert row.details["error_message"] == "fileCreatedAt must be a date"
    assert any("a.jpg" in w and "[rejected]" in w and "fileCreatedAt must be a date" in w for w in warnings)

    row.error_code = "not_sent"  # what Retry failed does
    session.commit()
    monkeypatch.setitem(adapters.BUILTINS, "folder_target", builtin)
    run_sync(session, target.id, run_context)

    row = sync_repository.rows(session, target.id)[0]
    assert row.status == SYNC_ITEM_SENT and "error_message" not in (row.details or {})


def test_the_builder_reads_real_failures_without_filenames(session, add_photo, album, make_target, run_context,
                                                          monkeypatch):
    from yaffo.site_agents.adapter.tool_providers.adapter_tool import AdapterToolProvider
    from yaffo.sync import adapters
    target = make_target(album("For Mom", [add_photo("Secret-Name.jpg"),
                                           add_photo("undated.jpg", date_taken=None, color=(9, 9, 9))]))
    builtin = adapters.BUILTINS["folder_target"]
    monkeypatch.setitem(adapters.BUILTINS, "folder_target",
                        adapters.BuiltinAdapter(builtin.role, builtin.options, RejectingTarget))
    run_sync(session, target.id, run_context)
    session.add(Job(id="run-1", name=target.name, status="COMPLETED", automation_id=target.id,
                    job_data='{"outcome": "sync_sent", "sent": 0, "failed": 2}'))
    session.commit()

    result = AdapterToolProvider(2, session).call_tool("read_sync_failures", {})

    text = result.model_text
    assert "For Mom to folder" in text and '"status": "COMPLETED"' in text and '"outcome": "sync_sent"' in text
    assert text.count("fileCreatedAt must be a date") == 2 and '"code": "rejected"' in text
    assert '"taken_at": {"local": "2024-07-04T18:22:10"}' in text and '"taken_at": {}' in text
    assert '"type": "image/jpeg"' in text
    assert "Secret-Name" not in text and "undated.jpg" not in text and "__yaffo_" not in text


def test_the_builder_is_told_when_no_sync_uses_the_service(session):
    from yaffo.site_agents.adapter.tool_providers.adapter_tool import AdapterToolProvider
    assert "No sync uses this service yet" in AdapterToolProvider(2, session).call_tool(
        "read_sync_failures", {}).model_text


def test_a_copy_deleted_in_the_folder_is_not_sent_again(session, add_photo, album, make_target, destination,
                                                        run_context):
    target = make_target(album("For Mom", [add_photo("a.jpg")]))
    run_sync(session, target.id, run_context)
    (destination / "For Mom" / "a.jpg").unlink()
    column = sync_repository.load_sync(target)
    sync_repository.save_runtime(target, column.runtime.with_cursor("reconcile", column.runtime.cursor("reconcile")
                                                                    .__class__()))
    session.commit()

    result = run_sync(session, target.id, run_context)

    assert result.params["removed_remotely"] == 1 and result.params["sent"] == 0
    assert files_in(destination) == []
    assert sync_repository.rows(session, target.id)[0].status == SYNC_ITEM_REMOVED_REMOTELY


def test_changing_the_folder_needs_consent_again(session, add_photo, album, make_target, tmp_path, run_context):
    target = make_target(album("For Mom", [add_photo("a.jpg")]))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    setup.set_options(session, target, {"folder": str(elsewhere)})

    assert target.enabled is False
    result = run_sync(session, target.id, run_context)

    assert result.problem == codes.PROBLEM_SYNC_NEEDS_CONSENT
    assert runtime(target).status == RUNTIME_NEEDS_CONSENT
    assert files_in(elsewhere) == []


def test_changing_target_folder_moves_an_existing_copy_after_consent(
        session, add_photo, album, make_target, destination, tmp_path, trash, run_context):
    target = make_target(album("For Mom", [add_photo("a.jpg")]))
    run_sync(session, target.id, run_context)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    setup.set_options(session, target, {"folder": str(elsewhere)})
    assert sync_repository.rows(session, target.id)[0].status == SYNC_ITEM_REMOVING
    setup.turn_on(session, target)
    moved = run_sync(session, target.id, run_context)

    assert (moved.params["removed"], moved.params["sent"]) == (1, 1)
    assert files_in(destination) == []
    assert files_in(elsewhere) == ["For Mom/a.jpg"]


def test_an_unplugged_drive_pauses_until_it_is_back(session, add_photo, album, make_target, destination,
                                                     run_context):
    target = make_target(album("For Mom", [add_photo("a.jpg")]))
    parked = destination.with_name("parked")
    destination.rename(parked)

    result = run_sync(session, target.id, run_context)

    assert result.problem == codes.PROBLEM_SYNC_PAUSED
    assert result.problem_params == {"pause_code": PAUSE_DESTINATION_MISSING}
    assert runtime(target).status == RUNTIME_PAUSED
    assert sync_repository.rows(session, target.id) == []

    parked.rename(destination)
    assert run_sync(session, target.id, run_context).params["sent"] == 1
    assert runtime(target).status == RUNTIME_READY


def test_a_deleted_album_pauses_instead_of_removing_everything(session, add_photo, album, make_target,
                                                                destination, run_context):
    mom = album("For Mom", [add_photo("a.jpg")])
    target = make_target(mom)
    run_sync(session, target.id, run_context)
    session.query(AlbumItem).delete()
    session.delete(mom)
    session.commit()

    result = run_sync(session, target.id, run_context)

    assert result.problem_params == {"pause_code": PAUSE_SELECTION_MISSING}
    assert files_in(destination) == ["For Mom/a.jpg"]


def test_a_folder_inside_the_library_is_refused(session, add_photo, album, library, run_context):
    target = setup.create_sync(session, "Loop", adapter_id=2)
    setup.set_options(session, target, {"folder": str(library / "exports")})
    (library / "exports").mkdir()
    setup.set_selection(session, target, Selection(kind="album", album_id=album("A", [add_photo("a.jpg")]).id))
    setup.turn_on(session, target)

    result = run_sync(session, target.id, run_context)

    assert result.problem == codes.PROBLEM_SYNC_INVALID_CONFIG
    assert os.listdir(library / "exports") == []


def test_a_crashed_runs_temporary_files_are_swept_but_not_another_syncs(session, add_photo, album, make_target,
                                                                        destination, run_context):
    target = make_target(album("For Mom", [add_photo("a.jpg")]))
    (destination / "For Mom").mkdir()
    mine = destination / "For Mom" / f".yaffo-{target.id}-dead.part"
    theirs = destination / f".yaffo-{target.id + 100}-busy.part"
    mine.write_bytes(b"half a photo")
    theirs.write_bytes(b"another sync is writing this")

    run_sync(session, target.id, run_context)

    assert not mine.exists()
    assert theirs.exists()
    assert files_in(destination) == [theirs.name, "For Mom/a.jpg"]
