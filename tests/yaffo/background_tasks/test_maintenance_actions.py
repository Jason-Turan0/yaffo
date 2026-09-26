"""The assistant's maintenance host functions (automation_sandbox/maintenance_actions.py)
and the scan/sync task they start (tasks/library_scan.py). Queue calls are patched:
these tests never touch the real task queue."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox import automation_actions
from yaffo.background_tasks.automation_sandbox import maintenance_actions as maintenance
from yaffo.background_tasks.tasks import library_scan
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


# ---- retry_job -------------------------------------------------------------------------

def _job(session, job_id="j1", name="index_photos", status=JOB_STATUS_FAILED, files=()):
    key = "files_to_index" if name == "index_photos" else "files_to_import"
    session.add(Job(id=job_id, name=name, status=status, job_data=json.dumps({key: list(files)})))
    session.commit()


def test_retry_job_queues_only_the_files_still_not_indexed(session, library, monkeypatch):
    paths = [str((library / n).resolve()) for n in ("a.jpg", "b.jpg", "c.jpg")]
    _job(session, files=paths)
    queued = []
    monkeypatch.setattr(maintenance, "enqueue_index_jobs",
                        lambda s, files: queued.append(files) or IndexJobs("imp", "idx"))

    assert maintenance.job_retryable(["j1"], session) is None
    assert maintenance.retry_job(session, "j1") == "idx"
    assert queued == [paths[1:]]


@pytest.mark.parametrize("job, message", [
    (None, "No job with that id"),
    ({"name": "find_duplicates"}, "Only import and index jobs"),
    ({"status": JOB_STATUS_PENDING}, "still running"),
    ({"files": ["__indexed__"]}, "already indexed"),
])
def test_retry_job_refuses_what_it_cannot_retry(session, library, job, message):
    if job is not None:
        files = [str((library / "a.jpg").resolve())] if job.pop("files", None) else ["/x.jpg"]
        _job(session, files=files, **job)
    assert message in maintenance.job_retryable(["j1"], session)


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


# ---- scans and syncs -------------------------------------------------------------------

def test_scan_and_sync_create_a_job_and_queue_the_task(session, library, monkeypatch):
    queued = []
    monkeypatch.setattr(library_scan, "library_scan_task", lambda job_id, apply: queued.append((job_id, apply)))

    scan_id = maintenance.start_library_scan(session)
    sync_id = maintenance.run_sync(session)

    assert queued == [(scan_id, False), (sync_id, True)]
    assert {j.name: j.status for j in session.query(Job)} == {
        maintenance.JOB_NAME_SCAN: JOB_STATUS_PENDING, maintenance.JOB_NAME_SYNC: JOB_STATUS_PENDING}


def test_sync_is_refused_while_a_media_folder_is_disconnected(session, library):
    assert maintenance.library_syncable([], session) is None
    for path in library.iterdir():
        path.unlink()
    library.rmdir()
    assert "aren't connected" in maintenance.library_syncable([], session)
    assert maintenance.library_scannable([], session) is None  # a scan is still fine


def _run(session, apply):
    session.add(Job(id="scan", name="library_scan", status=JOB_STATUS_PENDING))
    session.commit()
    library_scan.run_library_scan(session, "scan", apply)
    job = session.get(Job, "scan")
    session.refresh(job)
    return job


def test_a_scan_reports_what_it_found_and_changes_nothing(session, library):
    session.add(MediaItem(id=2, full_file_path=str(library / "gone.jpg"), status=MEDIA_STATUS_INDEXED))
    session.commit()

    job = _run(session, apply=False)

    assert job.status == JOB_STATUS_COMPLETED and job.completed_at is not None
    assert job.message.startswith("2 file(s) in the media folders aren't indexed yet; 1 indexed item(s)")
    assert "gone.jpg (missing)" in job.message
    assert json.loads(job.job_data)["unindexed"] == 2
    assert session.query(MediaItem).count() == 2


def test_a_sync_applies_the_scan(session, library, monkeypatch):
    synced = []
    monkeypatch.setattr(library_scan, "perform_sync",
                        lambda s, index, orphans, thumbs: synced.append((len(index), orphans)) or IndexJobs("imp", "idx"))

    job = _run(session, apply=True)

    assert synced == [(2, [])]
    assert job.status == JOB_STATUS_COMPLETED and "index job idx" in job.message


def test_a_sync_that_would_remove_too_much_is_refused(session, library, monkeypatch):
    session.add_all([MediaItem(id=i, full_file_path=str(library / f"gone{i}.jpg"), status=MEDIA_STATUS_INDEXED)
                     for i in range(10, 10 + library_scan.SYNC_REMOVE_FLOOR + 1)])
    session.commit()
    monkeypatch.setattr(library_scan, "perform_sync", lambda *args: pytest.fail("must not sync"))

    job = _run(session, apply=True)

    assert job.status == JOB_STATUS_FAILED and job.error.startswith("Refused: the sync would remove 26 of 27")
    assert session.query(MediaItem).count() == 27


def test_removal_limit_is_a_share_of_the_library_with_a_floor():
    assert library_scan.removal_limit(10) == library_scan.SYNC_REMOVE_FLOOR
    assert library_scan.removal_limit(10_000) == 1000


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
