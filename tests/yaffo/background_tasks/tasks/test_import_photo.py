"""Unit tests for import_photo_task: the create-Photo-rows stage.

Covers the happy path, missing-file accounting, cancellation, and -- the case our
crash discussion turned on -- a replay. Unlike index_photo, Photo.full_file_path is
unique, so a requeued import can't create duplicate rows; the unique constraint
turns the replay into an IntegrityError that the task records as a failed batch.
This pins that behavior so it doesn't silently change.
"""
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import (
    Job, MediaItem,
    JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_CANCELLED,
)
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.utils import SessionFactory, engine as prod_engine
import yaffo.background_tasks.tasks.import_photo as import_photo_mod
from yaffo.background_tasks.tasks.import_photo import import_photo_task

pytestmark = pytest.mark.unit


@pytest.fixture
def db_for_import(tmp_path):
    """A temp DB bound to the global SessionFactory, queue run inline."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={'check_same_thread': False},
    )
    db.metadata.create_all(engine)
    SessionFactory.configure(bind=engine)
    task_queue.immediate = True  # run the queue-wrapped task inline, not enqueued
    try:
        yield engine
    finally:
        task_queue.immediate = False
        SessionFactory.remove()
        SessionFactory.configure(bind=prod_engine)
        engine.dispose()


def _make_files(tmp_path: Path, n: int) -> list[str]:
    paths = []
    for i in range(n):
        p = tmp_path / f"photo_{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff")
        paths.append(str(p))
    return paths


def _make_job(engine, job_id: str, status: str = JOB_STATUS_PENDING, task_count: int = 0):
    with Session(engine) as s:
        s.add(Job(id=job_id, name="import_photos", status=status, task_count=task_count))
        s.commit()


def test_imports_valid_files_creates_photo_rows(db_for_import, tmp_path):
    engine = db_for_import
    files = _make_files(tmp_path, 3)
    _make_job(engine, "job-1", status=JOB_STATUS_PENDING, task_count=3)

    import_photo_task("job-1", files)

    with Session(engine) as s:
        photos = s.query(MediaItem).all()
        assert {p.full_file_path for p in photos} == set(files)
        job = s.query(Job).filter_by(id="job-1").one()
        assert job.completed_count == 3
        assert job.error_count == 0
        assert job.status == JOB_STATUS_RUNNING  # PENDING -> RUNNING on first batch


def test_missing_files_counted_as_errors_not_imported(db_for_import, tmp_path):
    engine = db_for_import
    present = _make_files(tmp_path, 2)
    missing = [str(tmp_path / "gone_a.jpg"), str(tmp_path / "gone_b.jpg")]
    _make_job(engine, "job-1", task_count=4)

    import_photo_task("job-1", present + missing)

    with Session(engine) as s:
        photos = s.query(MediaItem).all()
        assert {p.full_file_path for p in photos} == set(present)  # only real files
        job = s.query(Job).filter_by(id="job-1").one()
        assert job.completed_count == 2
        assert job.error_count == 2


def test_cancelled_job_before_start_imports_nothing(db_for_import, tmp_path):
    engine = db_for_import
    files = _make_files(tmp_path, 3)
    _make_job(engine, "job-1", status=JOB_STATUS_CANCELLED, task_count=3)

    import_photo_task("job-1", files)

    with Session(engine) as s:
        assert s.query(MediaItem).count() == 0
        job = s.query(Job).filter_by(id="job-1").one()
        assert job.completed_count == 0  # untouched -- returned before any work


def test_midbatch_cancellation_records_remaining_as_cancelled(db_for_import, tmp_path, monkeypatch):
    """Flipping the job to CANCELLED mid-run stops importing and books the unreached
    files as cancelled (checked every 5 files, so the cancel lands at index 5)."""
    engine = db_for_import
    files = _make_files(tmp_path, 8)
    _make_job(engine, "job-1", task_count=8)

    # RUNNING for the pre-loop check and index 0; CANCELLED from the index-5 check on.
    statuses = iter([JOB_STATUS_RUNNING, JOB_STATUS_RUNNING, JOB_STATUS_CANCELLED])
    monkeypatch.setattr(import_photo_mod, "get_job_status", lambda job_id: next(statuses))

    import_photo_task("job-1", files)

    with Session(engine) as s:
        assert s.query(MediaItem).count() == 5  # files 0..4 imported before the cancel
        job = s.query(Job).filter_by(id="job-1").one()
        assert job.completed_count == 5
        assert job.cancelled_count == 3  # 8 - 5 unreached


def test_replay_does_not_duplicate_photos(db_for_import, tmp_path):
    """A requeued import (host crash after commit, before the task was acked) re-runs
    the same batch. The unique constraint on full_file_path means no duplicate rows --
    instead the whole batch flushes as an IntegrityError and is booked as errors."""
    engine = db_for_import
    files = _make_files(tmp_path, 3)
    _make_job(engine, "job-1", task_count=3)

    import_photo_task("job-1", files)  # first pass
    import_photo_task("job-1", files)  # replay

    with Session(engine) as s:
        photos = s.query(MediaItem).all()
        assert len(photos) == 3  # no duplicates -- the constraint held
        job = s.query(Job).filter_by(id="job-1").one()
        assert job.completed_count == 3        # only the first pass's imports
        assert job.error_count == len(files)   # replay booked the whole batch as errors


def test_missing_job_returns_without_creating_photos(db_for_import, tmp_path):
    engine = db_for_import
    files = _make_files(tmp_path, 2)
    # no Job row inserted

    import_photo_task("does-not-exist", files)

    with Session(engine) as s:
        assert s.query(MediaItem).count() == 0
