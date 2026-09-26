"""Assistant maintenance functions and the legacy library-scan task. Queue calls
are patched; these tests never touch the real task queue."""
import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox import automation_actions
from yaffo.background_tasks.automation_sandbox import maintenance_actions as maintenance
from yaffo.background_tasks.tasks import library_scan
from yaffo.utils import file_sync
from yaffo.db import db
from yaffo.db.models import (
    FACE_STATUS_ASSIGNED,
    FACE_STATUS_IGNORED,
    FACE_STATUS_PROCESSING,
    FACE_STATUS_UNASSIGNED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    MEDIA_STATUS_INDEXED,
    ApplicationSettings,
    Automation,
    Face,
    Job,
    MediaItem,
    Person,
    PersonFace,
)
from yaffo.utils.index_jobs_dto import IndexJobs

pytestmark = pytest.mark.unit


@pytest.fixture
def emitted(monkeypatch):
    events = []
    monkeypatch.setattr(automation_actions, "emit_event", lambda kind, payload: events.append(payload))
    return events


@pytest.fixture
def session(tmp_path, emitted):
    engine = create_engine(f"sqlite:///{tmp_path / 'lib.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def library(session, tmp_path):
    """One media folder with three files (a.jpg indexed, b.jpg and c.jpg not yet),
    and a thumbnail folder."""
    media = tmp_path / "media"
    media.mkdir()
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (media / name).write_bytes(b"x")
    thumbs = tmp_path / "thumbs"
    session.add_all([
        ApplicationSettings(name="media_dirs", type="json", value=json.dumps([{"id": "m1", "path": str(media)}])),
        ApplicationSettings(name="thumbnail_dir", type="string", value=str(thumbs)),
        MediaItem(id=1, full_file_path=str((media / "a.jpg").resolve()), status=MEDIA_STATUS_INDEXED),
    ])
    session.commit()
    return media


# ---- set_automation_enabled -----------------------------------------------------------

def test_set_automation_enabled_and_its_undo(session):
    session.add(Automation(slug="file-sync", name="File Sync", enabled=True))
    session.commit()
    assert maintenance.automation_exists(["nope"], session) == "No automation with that slug"

    inverse = maintenance.undo_set_automation_enabled(["file-sync", False], session)
    maintenance.set_automation_enabled(session, "file-sync", False)
    assert session.query(Automation).one().enabled is False
    assert [(c.name, c.args) for c in inverse] == [("set_automation_enabled", ["file-sync", True, False])]

    maintenance.set_automation_enabled(session, *inverse[0].args)
    assert session.query(Automation).one().enabled is True
    maintenance.set_automation_enabled(session, "file-sync", False, True)  # expected matches: applies
    maintenance.set_automation_enabled(session, "file-sync", True, True)   # flipped since: skipped
    assert session.query(Automation).one().enabled is False
    assert maintenance.undo_set_automation_enabled(["file-sync", False], session) == []  # already off


def test_run_automation_uses_whole_library_context(session, library, monkeypatch):
    automation = Automation(slug="export_photo_tag", name="Export photo tag", enabled=False,
                            is_system=True, handler="export_photo_tag")
    session.add(automation)
    session.commit()
    calls = []
    monkeypatch.setattr(maintenance, "invoke_automation", lambda target, context: calls.append((target, context)) or True)

    assert maintenance.automation_runnable(["export_photo_tag"], session) is None
    assert maintenance.run_automation(session, "export_photo_tag") is None

    assert len(calls) == 1
    target, context = calls[0]
    assert target.id == automation.id
    assert context.event_type is None
    assert context.media_item_ids == [1]
    assert context.scope_paths == [str(library.resolve())]


def test_run_automation_rejects_missing_or_unpublished_automation(session):
    assert maintenance.automation_runnable(["missing"], session)
    session.add(Automation(slug="draft", name="Draft", enabled=True))
    session.commit()
    assert maintenance.automation_runnable(["draft"], session)
    with pytest.raises(ValueError, match="no runnable handler"):
        maintenance.run_automation(session, "draft")


def test_run_automation_resolves_directory_folder_and_file_scopes(session, library, monkeypatch):
    session.add_all([
        Automation(slug="export_photo_tag", name="Export photo tag", handler="export_photo_tag"),
        MediaItem(id=2, full_file_path=str(library / "b.jpg"), status=MEDIA_STATUS_INDEXED),
    ])
    session.commit()
    calls = []
    monkeypatch.setattr(maintenance, "invoke_automation", lambda target, context: calls.append(context) or True)

    maintenance.run_automation(session, "export_photo_tag", {"type": "media_dirs", "media_dir_ids": ["m1"]})
    assert calls[-1].media_item_ids == [1, 2]
    assert calls[-1].scope_paths == [str(library.resolve())]

    folder = library / "trip"
    folder.mkdir()
    (folder / "c.jpg").write_bytes(b"x")
    session.add(MediaItem(id=3, full_file_path=str(folder / "c.jpg"), status=MEDIA_STATUS_INDEXED))
    session.commit()
    maintenance.run_automation(session, "export_photo_tag", {"type": "folders", "folder_paths": [str(folder)]})
    assert calls[-1].media_item_ids == [3]
    assert calls[-1].scope_paths == [str(folder.resolve())]

    maintenance.run_automation(session, "export_photo_tag", {"type": "files", "media_item_ids": [2, 1]})
    assert calls[-1].media_item_ids == [1, 2]
    assert calls[-1].scope_paths == []


def test_run_automation_scope_rejects_unsafe_or_stale_selection(session, library, tmp_path):
    session.add_all([
        Automation(slug="export_photo_tag", name="Export photo tag", handler="export_photo_tag"),
        Automation(slug="file-sync", name="File sync", handler="file_sync"),
        MediaItem(id=2, full_file_path=str(tmp_path / "outside.jpg"), status=MEDIA_STATUS_INDEXED),
    ])
    session.commit()
    bad_scopes = [
        ("export_photo_tag", {"type": "media_dirs", "media_dir_ids": ["gone"]}),
        ("export_photo_tag", {"type": "folders", "folder_paths": [str(tmp_path / "outside")]}),
        ("export_photo_tag", {"type": "folders", "folder_paths": [str(library / "a.jpg")]}),
        ("export_photo_tag", {"type": "files", "media_item_ids": [404]}),
        ("export_photo_tag", {"type": "files", "media_item_ids": [2]}),
        ("export_photo_tag", {"type": "files", "media_item_ids": []}),
        ("file-sync", {"type": "files", "media_item_ids": [1]}),
    ]
    for slug, scope in bad_scopes:
        assert maintenance.automation_runnable([slug, scope], session)
        with pytest.raises(ValueError):
            maintenance.run_automation(session, slug, scope)


# ---- reindex_media --------------------------------------------------------------------

def test_reindex_media_skips_items_whose_file_is_gone(session, library, monkeypatch):
    session.add(MediaItem(id=2, full_file_path="/gone/b.jpg"))
    session.commit()
    received = []
    monkeypatch.setattr(maintenance, "reindex_media_items",
                        lambda s, items: received.append([i.id for i in items]) or IndexJobs("imp", "idx"))

    assert maintenance.reindex_media(session, [1, 2]) == "idx"
    assert received == [[1]]
    with pytest.raises(ValueError):
        maintenance.reindex_media(session, [2])


# ---- scan, then index_files / remove_missing_items ------------------------------------------

def _scan(session, job_id="scan"):
    session.add(Job(id=job_id, name=maintenance.JOB_NAME_SCAN, status=JOB_STATUS_PENDING))
    session.commit()
    library_scan.run_library_scan(session, job_id)
    job = session.get(Job, job_id)
    session.refresh(job)
    return job


def test_start_library_scan_creates_a_job_and_queues_the_task(session, library, monkeypatch):
    queued = []
    monkeypatch.setattr(library_scan, "library_scan_task", queued.append)

    job_id = maintenance.start_library_scan(session)

    assert queued == [job_id]
    assert session.get(Job, job_id).status == JOB_STATUS_PENDING


def test_a_scan_records_exactly_what_it_found_and_changes_nothing(session, library):
    session.add(MediaItem(id=2, full_file_path=str(library / "gone.jpg"), status=MEDIA_STATUS_INDEXED))
    session.commit()

    job = _scan(session)

    assert job.status == JOB_STATUS_COMPLETED and job.completed_at is not None
    assert job.message.startswith("2 file(s) in the media folders aren't indexed yet; 1 indexed item(s)")
    assert "gone.jpg (missing)" in job.message
    data = json.loads(job.job_data)
    assert sorted(Path(p).name for p in data["unindexed_paths"]) == ["b.jpg", "c.jpg"]
    assert data["missing"] == [{"id": 2, "reason": "missing"}]
    assert session.query(MediaItem).count() == 2


def test_an_empty_media_folder_is_reported_not_counted_as_missing(session, library, tmp_path):
    """A mount point that came back empty: every item under it looks deleted."""
    empty = tmp_path / "drive"
    empty.mkdir()
    setting = session.query(ApplicationSettings).filter_by(name="media_dirs").one()
    setting.value = json.dumps(json.loads(setting.value) + [{"id": "m2", "path": str(empty)}])
    session.add_all([MediaItem(id=i, full_file_path=str(empty / f"{i}.jpg"), status=MEDIA_STATUS_INDEXED)
                     for i in (10, 11)])
    session.commit()

    job = _scan(session)

    data = json.loads(job.job_data)
    assert data["missing"] == [] and data["empty_roots"] == [str(empty)]
    assert "hold no media files at all" in job.message and "2 indexed item(s) aren't counted" in job.message


def test_index_files_indexes_what_the_scan_found(session, library, monkeypatch):
    job = _scan(session)
    queued = []
    monkeypatch.setattr(maintenance, "enqueue_index_jobs",
                        lambda s, files: queued.append(sorted(Path(f).name for f in files)) or IndexJobs("i", "x"))

    assert maintenance.scan_has_files([job.id], session) is None
    assert maintenance.index_files(session, job.id) == "x"
    assert queued == [["b.jpg", "c.jpg"]]


@pytest.mark.parametrize("state, message", [
    ("unknown", "No scan job with that id"),
    ("running", "hasn't finished"),
    ("old", "more than a day old"),
])
def test_acting_on_a_scan_needs_a_recent_finished_one(session, library, state, message):
    job_id = "nope"
    if state != "unknown":
        job = _scan(session)
        job_id = job.id
        if state == "running":
            job.status = JOB_STATUS_PENDING
        else:
            job.completed_at = job.completed_at - maintenance.SCAN_MAX_AGE - timedelta(minutes=1)
        session.commit()
    assert message in maintenance.scan_has_files([job_id], session)
    assert message in maintenance.scan_has_missing([job_id], session)


def test_remove_missing_items_removes_only_what_is_still_missing(session, library, tmp_path):
    for i in (2, 3, 4):
        session.add(MediaItem(id=i, full_file_path=str((library / f"gone{i}.jpg").resolve()),
                              status=MEDIA_STATUS_INDEXED))
    session.commit()
    job = _scan(session)
    assert sorted(maintenance.scan_missing_ids(session, job.id)) == [2, 3, 4]
    assert maintenance.scan_missing_ids(session, job.id, [3, 99]) == [3]
    (library / "gone4.jpg").write_bytes(b"x")  # back since the scan

    assert maintenance.remove_missing_items(session, job.id) == 2

    assert sorted(i.id for i in session.query(MediaItem)) == [1, 4]


def test_remove_missing_items_keeps_items_when_their_folder_disconnects(session, library):
    session.add(MediaItem(id=2, full_file_path=str((library / "gone.jpg").resolve()), status=MEDIA_STATUS_INDEXED))
    session.commit()
    job = _scan(session)
    for path in library.iterdir():  # the drive comes back empty before approval
        path.unlink()

    assert maintenance.remove_missing_items(session, job.id) == 0
    assert session.query(MediaItem).count() == 2


def test_the_unattended_sync_leaves_items_under_an_empty_folder_alone(session, library, tmp_path, monkeypatch):
    empty = tmp_path / "drive"
    empty.mkdir()
    setting = session.query(ApplicationSettings).filter_by(name="media_dirs").one()
    setting.value = json.dumps(json.loads(setting.value) + [{"id": "m2", "path": str(empty)}])
    session.add_all([
        MediaItem(id=10, full_file_path=str(empty / "10.jpg"), status=MEDIA_STATUS_INDEXED),
        MediaItem(id=11, full_file_path=str((library / "gone.jpg").resolve()), status=MEDIA_STATUS_INDEXED),
    ])
    session.commit()
    synced = []
    monkeypatch.setattr(file_sync, "perform_sync",
                        lambda s, index, orphans, thumbs, automation_id=None:
                        synced.append(orphans) or IndexJobs("i", "x"))

    session.add(Automation(id=5, slug="file-sync", name="File sync", enabled=True))
    session.commit()

    file_sync.run_file_sync(session, automation_id=5)

    assert synced == [[11]]  # the item under the empty drive stays
    run = session.query(Job).filter_by(name=file_sync.FILE_SYNC_JOB).one()
    assert run.automation_id == 5 and run.status == JOB_STATUS_COMPLETED and run.error_count == 1
    assert str(empty) in run.error and "The 1 indexed item(s) under them were left" in run.error
    assert json.loads(run.job_data) == {
        "outcome": "started", "indexed": 2, "removed": 1, "held_back": 1, "empty_roots": [str(empty)],
        "import_job_id": "i", "index_job_id": "x"}


def test_the_unattended_sync_records_a_run_even_with_nothing_else_to_do(session, tmp_path, monkeypatch):
    empty = tmp_path / "drive"
    empty.mkdir()
    session.add_all([
        ApplicationSettings(name="media_dirs", type="json", value=json.dumps([{"id": "m1", "path": str(empty)}])),
        ApplicationSettings(name="thumbnail_dir", type="string", value=str(tmp_path / "thumbs")),
        MediaItem(id=1, full_file_path=str(empty / "1.jpg"), status=MEDIA_STATUS_INDEXED),
    ])
    session.commit()
    monkeypatch.setattr(file_sync, "perform_sync", lambda *a, **k: pytest.fail("nothing to sync"))

    assert file_sync.run_file_sync(session) is None
    run = session.query(Job).filter_by(name=file_sync.FILE_SYNC_JOB).one()
    assert json.loads(run.job_data)["outcome"] == "in_sync" and run.error_count == 1
    assert session.query(MediaItem).count() == 1


def test_every_file_sync_run_is_recorded_with_how_it_ended(session, library, monkeypatch):
    for path in library.iterdir():
        (library / path.name).unlink()
    session.query(MediaItem).delete()
    session.commit()

    file_sync.run_file_sync(session)  # nothing on disk, nothing indexed: in sync
    library.rmdir()
    file_sync.run_file_sync(session)  # the only media folder is gone: skipped

    runs = session.query(Job).filter_by(name=file_sync.FILE_SYNC_JOB).order_by(Job.created_at).all()
    assert [(json.loads(r.job_data)["outcome"], r.status) for r in runs] == [
        ("in_sync", JOB_STATUS_COMPLETED), ("no_folder_connected", JOB_STATUS_FAILED)]
    assert runs[0].error is None and "None of the media folders is connected" in runs[1].error


def test_a_file_sync_that_raises_is_recorded_as_failed(session, library, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("drive error")
    monkeypatch.setattr(file_sync, "scan_media_dirs", broken)

    with pytest.raises(OSError):
        file_sync.run_file_sync(session)

    run = session.query(Job).filter_by(name=file_sync.FILE_SYNC_JOB).one()
    assert run.status == JOB_STATUS_FAILED and "drive error" in run.error


# ---- repair_face_statuses ------------------------------------------------------------------

def test_repair_face_statuses(session, monkeypatch, emitted):
    session.add_all([Person(id=1, name="Chase"), MediaItem(id=1, full_file_path="/lib/1.jpg")])
    session.add_all([
        Face(id=1, media_item_id=1, status=FACE_STATUS_UNASSIGNED),  # linked, not assigned
        Face(id=2, media_item_id=1, status=FACE_STATUS_PROCESSING),  # stuck, linked
        Face(id=3, media_item_id=1, status=FACE_STATUS_PROCESSING),  # stuck, unlinked
        Face(id=4, media_item_id=1, status=FACE_STATUS_IGNORED),     # ignored, linked
        Face(id=5, media_item_id=1, status=FACE_STATUS_ASSIGNED),    # fine
    ])
    session.flush()
    session.add_all([PersonFace(person_id=1, face_id=i) for i in (1, 2, 4, 5)])
    session.commit()

    monkeypatch.setattr(maintenance, "face_tasks_active", lambda: True)
    assert maintenance.face_repair_counts(session).total == 2  # PROCESSING may be in flight
    monkeypatch.setattr(maintenance, "face_tasks_active", lambda: False)
    assert maintenance.face_repair_counts(session).total == 4
    assert maintenance.faces_need_repair([], session) is None

    maintenance.repair_face_statuses(session)

    session.expire_all()
    statuses = {f.id: f.status for f in session.query(Face)}
    assert statuses == {1: FACE_STATUS_ASSIGNED, 2: FACE_STATUS_ASSIGNED, 3: FACE_STATUS_UNASSIGNED,
                        4: FACE_STATUS_IGNORED, 5: FACE_STATUS_ASSIGNED}
    assert sorted(pf.face_id for pf in session.query(PersonFace)) == [1, 2, 5]
    assert emitted == [{"media_item_ids": [1]}]
    assert maintenance.faces_need_repair([], session) == "No faces need repairing"


def test_repair_leaves_processing_faces_alone_while_face_tasks_run(session, monkeypatch):
    session.add_all([MediaItem(id=1, full_file_path="/lib/1.jpg"),
                     Face(id=1, media_item_id=1, status=FACE_STATUS_PROCESSING)])
    session.commit()
    monkeypatch.setattr(maintenance, "face_tasks_active", lambda: True)

    maintenance.repair_face_statuses(session)

    assert session.get(Face, 1).status == FACE_STATUS_PROCESSING
