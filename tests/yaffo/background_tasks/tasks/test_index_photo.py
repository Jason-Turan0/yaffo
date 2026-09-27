"""Regression: index_photo_task replaces a photo's faces on a re-run instead of
accumulating duplicates.

A task requeued after a host crash is re-run from scratch (at-least-once), so a
replay must converge to the same faces, and the superseded thumbnail crops must be
unlinked. Face thumbnail paths carry a uuid, so the Face.full_file_path unique
constraint can't catch a re-insert -- without the clear-then-insert guard a replay
would silently double every face.
"""
import uuid
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import Job, MediaItem, Face, JOB_STATUS_RUNNING, MEDIA_STATUS_INDEXED
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.utils import SessionFactory, engine as prod_engine
import yaffo.background_tasks.tasks.index_photo as index_photo_mod
from yaffo.background_tasks.tasks.index_photo import index_photo_task

pytestmark = pytest.mark.unit


def _fake_index_photo_factory(thumbnail_dir: Path):
    """Stand in for the real (dlib-backed) index_photo: write a real crop file under
    a fresh uuid name each call -- exactly the non-deterministic path that defeats
    the unique constraint -- and return one face pointing at it."""
    def fake_index_photo(path: Path, thumb_dir: Path) -> dict:
        thumb = thumbnail_dir / f"face_{path.stem}_{uuid.uuid4().hex[:8]}.jpg"
        thumb.write_bytes(b"\xff\xd8\xff")
        return {
            "faces_data": [{
                "embedding": np.zeros(512, dtype=np.float32),
                "full_file_path": str(thumb),
                "location_top": 0, "location_right": 10,
                "location_bottom": 10, "location_left": 0,
                "estimated_age": 30, "gender": "M", "det_score": 0.9,
            }],
            "latitude": None, "longitude": None, "location_name": None,
            "date_taken": None, "year": None, "month": None, "device": None,
        }
    return fake_index_photo


@pytest.fixture
def db_for_index(tmp_path, monkeypatch):
    """A temp DB bound to the global SessionFactory, with the face-detection leaf and
    the thumbnail-dir lookup stubbed so the task runs without dlib."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={'check_same_thread': False},
    )
    db.metadata.create_all(engine)
    SessionFactory.configure(bind=engine)
    task_queue.immediate = True  # run the queue-wrapped task inline, not enqueued

    thumbnail_dir = tmp_path / "thumbs"
    thumbnail_dir.mkdir()
    monkeypatch.setattr(index_photo_mod, "get_current_thumbnail_dir", lambda: thumbnail_dir)
    monkeypatch.setattr(index_photo_mod, "index_photo", _fake_index_photo_factory(thumbnail_dir))

    try:
        yield engine, thumbnail_dir
    finally:
        task_queue.immediate = False
        SessionFactory.remove()
        SessionFactory.configure(bind=prod_engine)
        engine.dispose()


def test_reindex_replaces_faces_not_duplicates(db_for_index, tmp_path):
    engine, thumbnail_dir = db_for_index
    photo_file = tmp_path / "photo.jpg"
    photo_file.write_bytes(b"\xff\xd8\xff")
    job_id = "job-1"

    with Session(engine) as s:
        s.add(Job(id=job_id, name="index_photos", status=JOB_STATUS_RUNNING, task_count=1))
        s.add(MediaItem(full_file_path=str(photo_file)))
        s.commit()

    # First index pass.
    index_photo_task(job_id, [str(photo_file)])

    with Session(engine) as s:
        media_item = s.query(MediaItem).one()
        faces = s.query(Face).all()
        assert len(faces) == 1
        assert media_item.status == MEDIA_STATUS_INDEXED
        first_thumb = Path(faces[0].full_file_path)
    assert first_thumb.exists()

    # Replay the same task -- simulates a requeue after a host crash.
    index_photo_task(job_id, [str(photo_file)])

    with Session(engine) as s:
        faces = s.query(Face).all()
        assert len(faces) == 1                  # replaced, not doubled
        second_thumb = Path(faces[0].full_file_path)

    assert second_thumb.exists()
    assert second_thumb != first_thumb          # a fresh crop was written
    assert not first_thumb.exists()             # the superseded crop was unlinked
    assert list(thumbnail_dir.glob("*.jpg")) == [second_thumb]  # exactly one crop left


def _batch(engine, tmp_path, n, job_status=JOB_STATUS_RUNNING):
    files = []
    for i in range(n):
        f = tmp_path / f"photo_{i}.jpg"
        f.write_bytes(b"\xff\xd8\xff")
        files.append(str(f))
    with Session(engine) as s:
        s.add(Job(id="job-1", name="index_photos", status=job_status, task_count=n,
                  completed_count=0, error_count=0, cancelled_count=0))
        s.add_all(MediaItem(full_file_path=f) for f in files)
        s.commit()
    return files


def test_midbatch_cancellation_indexes_up_to_the_check_and_counts_the_rest(db_for_index, tmp_path, monkeypatch):
    """Scenario 8: the index task checks for a cancel every 5 photos; one cancelled
    at the second check indexes the first 5 and counts the other 7 as cancelled."""
    engine, _ = db_for_index
    files = _batch(engine, tmp_path, 12)
    statuses = iter([JOB_STATUS_RUNNING, "CANCELLED"])  # the start check, then the check at photo 5
    monkeypatch.setattr(index_photo_mod, "get_job_status", lambda job_id: next(statuses))

    index_photo_task("job-1", files)

    with Session(engine) as s:
        job = s.get(Job, "job-1")
        assert (job.completed_count, job.cancelled_count) == (5, 7)
        assert s.query(MediaItem).filter_by(status=MEDIA_STATUS_INDEXED).count() == 5


def test_an_index_batch_stamps_its_start_and_refreshes_the_estimate(db_for_index, tmp_path):
    """Scenario 47 (index): a batch's progress write sets started_at and
    estimated_completed_at."""
    engine, _ = db_for_index
    files = _batch(engine, tmp_path, 3)
    with Session(engine) as s:
        job = s.get(Job, "job-1")
        job.task_count = 6  # half the job still to go
        s.commit()

    index_photo_task("job-1", files)

    with Session(engine) as s:
        job = s.get(Job, "job-1")
        assert job.started_at is not None
        assert job.estimated_completed_at is not None and job.estimated_completed_at >= job.started_at


def test_a_permanent_failure_marks_the_item_failed_and_a_success_clears_it(db_for_index, tmp_path, monkeypatch):
    from yaffo.db.models import MEDIA_STATUS_FAILED
    from yaffo.utils.index_errors import IndexFailure, file_signature
    engine, thumbnail_dir = db_for_index
    files = _batch(engine, tmp_path, 1)
    real_index_photo = index_photo_mod.index_photo
    monkeypatch.setattr(index_photo_mod, "index_photo", lambda path, thumbs: IndexFailure(
        "decode_error", "OSError: image file is truncated", permanent=True))

    index_photo_task("job-1", files)

    with Session(engine) as s:
        item = s.query(MediaItem).one()
        assert item.status == MEDIA_STATUS_FAILED
        assert (item.index_error, item.index_error_detail) == ("decode_error", "OSError: image file is truncated")
        assert item.index_failed_at is not None and item.index_failed_signature == file_signature(Path(files[0]))
        assert s.get(Job, "job-1").error_count == 1

    monkeypatch.setattr(index_photo_mod, "index_photo", real_index_photo)  # the stubbed success
    index_photo_task("job-1", files)  # the user retries

    with Session(engine) as s:
        item = s.query(MediaItem).one()
        assert item.status == MEDIA_STATUS_INDEXED
        assert (item.index_error, item.index_error_detail, item.index_failed_at, item.index_failed_signature) == (
            None, None, None, None)


def test_a_file_that_couldnt_be_reached_stays_imported_for_the_next_sync(db_for_index, tmp_path, monkeypatch):
    from yaffo.utils.index_errors import IndexFailure
    engine, _ = db_for_index
    files = _batch(engine, tmp_path, 1)
    monkeypatch.setattr(index_photo_mod, "index_photo", lambda path, thumbs: IndexFailure(
        "unreadable", "OSError: [Errno 5] Input/output error", permanent=False))

    index_photo_task("job-1", files)

    with Session(engine) as s:
        item = s.query(MediaItem).one()
        assert item.status == "IMPORTED" and item.index_error is None
