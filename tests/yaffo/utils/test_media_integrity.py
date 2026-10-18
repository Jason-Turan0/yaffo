"""Deletion and file sync repair dependents without relying on FK cascades."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import (
    ClassificationLabel, Face, Job, MediaItem, MediaLabel, Person, PersonFace, Tag,
    MEDIA_STATUS_INDEXED,
)
from yaffo.db.repositories.media_integrity_repository import sweep_orphaned_media_dependents
from yaffo.utils import file_sync
from yaffo.utils.index_jobs_dto import IndexJobs
from yaffo.utils.index_photos import delete_orphaned_media_items

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'integrity.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            Person(id=1, name="Alice", avg_embedding=b"unchanged"),
            ClassificationLabel(id=1, name="Outdoors"),
        ])
        for item_id in (1, 2):
            session.add(MediaItem(id=item_id, full_file_path=str(tmp_path / f"{item_id}.jpg"),
                                  status=MEDIA_STATUS_INDEXED))
            session.add(Face(id=item_id, media_item_id=item_id,
                             full_file_path=str(tmp_path / f"face{item_id}.jpg")))
            session.add(PersonFace(person_id=1, face_id=item_id))
            session.add(MediaLabel(media_item_id=item_id, label_id=1, confidence=0.9))
            session.add(Tag(media_item_id=item_id, tag_name="trip"))
        session.commit()
        yield session
    engine.dispose()


def assert_only_live_dependents(session: Session) -> None:
    assert [r.face_id for r in session.query(PersonFace)] == [1]
    assert [r.media_item_id for r in session.query(MediaLabel)] == [1]
    assert [r.media_item_id for r in session.query(Tag)] == [1]
    assert session.get(Person, 1).avg_embedding == b"unchanged"
    assert session.query(ClassificationLabel).count() == 1


def test_deleting_missing_media_removes_all_dependents(session: Session, tmp_path: Path, monkeypatch):
    monkeypatch.setattr("yaffo.utils.index_photos.announce_deleted", lambda *args: None)
    crop = tmp_path / "face2.jpg"
    crop.write_bytes(b"crop")
    (tmp_path / "face1.jpg").write_bytes(b"keep")

    assert delete_orphaned_media_items(session, [2]) == 1
    assert_only_live_dependents(session)
    assert session.query(Face).count() == session.query(MediaItem).count() == 1
    assert not crop.exists()
    assert (tmp_path / "face1.jpg").exists()
    assert delete_orphaned_media_items(session, [2]) == 0


def leave_legacy_orphans(session: Session) -> None:
    session.query(MediaItem).filter_by(id=2).delete(synchronize_session=False)
    session.add(PersonFace(person_id=1, face_id=999))
    session.commit()


def test_sweep_repairs_legacy_data_and_is_repeatable(session: Session):
    leave_legacy_orphans(session)
    assert sweep_orphaned_media_dependents(session) == {"person_links": 2, "labels": 1, "tags": 1}
    session.commit()
    assert_only_live_dependents(session)
    assert sweep_orphaned_media_dependents(session) == {"person_links": 0, "labels": 0, "tags": 0}


@pytest.mark.parametrize("has_work", [False, True])
def test_file_sync_sweeps_even_when_files_are_in_sync(session: Session, tmp_path: Path, monkeypatch, has_work: bool):
    leave_legacy_orphans(session)
    monkeypatch.setattr(file_sync, "get_media_dirs", lambda s: [tmp_path])
    monkeypatch.setattr(file_sync, "get_thumbnail_dir", lambda s: tmp_path / "thumbs")
    monkeypatch.setattr(file_sync, "active_library_jobs", lambda s: False)
    scan = file_sync.MediaScan(
        unindexed=[{"full_path": str(tmp_path / "new.jpg")}] if has_work else [],
        orphaned=[], total_imported=1, total_indexed=1, total_filesystem=1,
    )
    monkeypatch.setattr(file_sync, "scan_media_dirs", lambda *args, **kwargs: scan)
    monkeypatch.setattr(file_sync, "perform_sync", lambda *args, **kwargs: IndexJobs("i", "x"))

    file_sync.run_file_sync(session)
    assert_only_live_dependents(session)
    session.expire_all()
    assert_only_live_dependents(session)


def test_idle_file_sync_cleans_thumbnails_and_queues_missing_repairs(session: Session, tmp_path: Path, monkeypatch):
    leave_legacy_orphans(session)
    (tmp_path / "1.jpg").write_bytes(b"original")
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    crop = thumbs / "face1.jpg"
    poster = thumbs / "poster.jpg"
    poster.write_bytes(b"keep")
    orphan = thumbs / "unused.jpg"
    orphan.write_bytes(b"remove")
    session.get(Face, 1).full_file_path = str(crop)
    session.get(MediaItem, 1).poster_path = str(poster)
    session.commit()
    monkeypatch.setattr(file_sync, "get_media_dirs", lambda s: [tmp_path])
    monkeypatch.setattr(file_sync, "get_thumbnail_dir", lambda s: thumbs)
    monkeypatch.setattr(file_sync, "perform_sync", lambda *args, **kwargs: pytest.fail("index already in sync"))
    repairs = []

    def start_repair(s: Session, missing) -> str:
        repairs.append(missing)
        return "repair-1"

    monkeypatch.setattr(file_sync, "start_thumbnail_repair", start_repair)

    assert file_sync.run_file_sync(session) is None
    assert_only_live_dependents(session)
    assert not orphan.exists()
    assert poster.read_bytes() == b"keep"
    assert len(repairs) == 1
    assert repairs[0].media_item_ids == [1]
    assert repairs[0].faces == 1 and repairs[0].posters == 0
    run = session.query(Job).filter_by(name=file_sync.FILE_SYNC_JOB).one()
    assert '"thumbnail_repair_job_id": "repair-1"' in run.job_data

    crop.write_bytes(b"repaired")
    file_sync.run_file_sync(session)
    assert len(repairs) == 1
    assert crop.exists() and poster.exists()


@pytest.mark.parametrize("interruption", ["cancelled", "busy"])
def test_idle_file_sync_skips_maintenance_if_interrupted_during_scan(
    session: Session, tmp_path: Path, monkeypatch, interruption: str,
):
    leave_legacy_orphans(session)
    monkeypatch.setattr(file_sync, "get_media_dirs", lambda s: [tmp_path])
    monkeypatch.setattr(file_sync, "get_thumbnail_dir", lambda s: tmp_path / "thumbs")

    def scan(s: Session, *args, **kwargs) -> file_sync.MediaScan:
        if interruption == "cancelled":
            s.query(Job).filter_by(name=file_sync.FILE_SYNC_JOB).update({"status": "CANCELLED"})
        else:
            s.add(Job(id="other-index", name="index_photos", status="PENDING"))
        s.commit()
        return file_sync.MediaScan([], [], 1, 1, 1)

    monkeypatch.setattr(file_sync, "scan_media_dirs", scan)
    monkeypatch.setattr(file_sync, "delete_orphaned_thumbnails", lambda *args: pytest.fail("interrupted"))
    monkeypatch.setattr(file_sync, "start_thumbnail_repair", lambda *args: pytest.fail("interrupted"))
    file_sync.run_file_sync(session)
    assert session.query(PersonFace).count() == 3
