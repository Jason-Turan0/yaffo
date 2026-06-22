"""Integration tests for the index-jobs chord pipeline (utils/index_jobs).

enqueue_index_jobs dispatches a chord pipeline: import members run as a
group, their completion finalizes the import job and triggers start_index_stage,
which runs the index members as a second group whose completion finalizes the
index job. These tests drive the *real* pipeline in immediate mode against a
throwaway SQLite DB, mocking only the heavy face-recognition leaf (index_photo).

Because index_photo_task reads the Photo rows that import_photo_task creates,
asserting the photos end up INDEXED also proves import ran before index -- the
ordering guarantee the chord exists to provide.
"""
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import (
    MediaItem, Job,
    JOB_STATUS_COMPLETED, PHOTO_STATUS_INDEXED,
)
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.utils import SessionFactory, engine as prod_engine
import yaffo.background_tasks.tasks.complete_job as complete_job
from yaffo.utils.index_jobs import enqueue_index_jobs

pytestmark = pytest.mark.unit


_EMPTY_INDEX_RESULT = {
    "faces_data": [],
    "latitude": None, "longitude": None, "location_name": None,
    "device": None,
    "date_taken": None, "year": None, "month": None,
}


@contextmanager
def _session(engine):
    sess = Session(engine)
    try:
        yield sess
    finally:
        sess.close()


@pytest.fixture
def immediate_db(tmp_path, monkeypatch):
    """Point the global SessionFactory at a temp DB, run the queue inline, and stub
    the face-recognition leaf. Records the (in-order) job ids whose completion
    event fired, so tests can assert import completes before index."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={'check_same_thread': False},
    )
    db.metadata.create_all(engine)
    SessionFactory.configure(bind=engine)
    task_queue.immediate = True

    monkeypatch.setattr(
        "yaffo.background_tasks.tasks.index_photo.index_photo",
        lambda path, thumb_dir: dict(_EMPTY_INDEX_RESULT),
    )
    completed_events: list[str] = []
    monkeypatch.setattr(
        complete_job, "emit_job_completed_event",
        lambda session, job: completed_events.append(job.name),
    )

    try:
        yield engine, completed_events
    finally:
        task_queue.immediate = False
        SessionFactory.remove()
        SessionFactory.configure(bind=prod_engine)
        engine.dispose()


def _make_files(tmp_path: Path, n: int) -> list[str]:
    files = []
    for i in range(n):
        p = tmp_path / f"photo_{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff")  # a real file; contents irrelevant (index_photo is stubbed)
        files.append(str(p))
    return files


def test_full_pipeline_imports_then_indexes(immediate_db, tmp_path):
    engine, completed_events = immediate_db
    files = _make_files(tmp_path, 3)

    with _session(engine) as session:
        result = enqueue_index_jobs(session, files)

    with _session(engine) as session:
        photos = session.query(MediaItem).all()
        assert {p.full_file_path for p in photos} == set(files)
        # all reached INDEXED -> index ran after import created the rows
        assert all(p.status == PHOTO_STATUS_INDEXED for p in photos)

        import_job = session.query(Job).filter_by(id=result.import_job_id).one()
        index_job = session.query(Job).filter_by(id=result.index_job_id).one()
        assert import_job.status == JOB_STATUS_COMPLETED
        assert index_job.status == JOB_STATUS_COMPLETED
        assert import_job.completed_count == 3
        assert index_job.completed_count == 3

    # both jobs finalized, import strictly before index
    assert completed_events == ["import_photos", "index_photos"]


def test_empty_import_still_indexes_existing(immediate_db, tmp_path):
    """Files already imported but not yet indexed: nothing to import (empty
    group), but the index stage must still run and complete both jobs."""
    engine, completed_events = immediate_db
    files = _make_files(tmp_path, 2)
    with _session(engine) as session:
        for f in files:
            session.add(MediaItem(full_file_path=f))  # pre-existing, un-indexed
        session.commit()

    with _session(engine) as session:
        result = enqueue_index_jobs(session, files)

    with _session(engine) as session:
        import_job = session.query(Job).filter_by(id=result.import_job_id).one()
        index_job = session.query(Job).filter_by(id=result.index_job_id).one()
        assert import_job.task_count == 0
        assert import_job.status == JOB_STATUS_COMPLETED
        assert index_job.task_count == 2
        assert index_job.status == JOB_STATUS_COMPLETED
        assert all(p.status == PHOTO_STATUS_INDEXED for p in session.query(MediaItem).all())

    assert completed_events == ["import_photos", "index_photos"]


def test_nothing_to_do_completes_both_jobs(immediate_db, tmp_path):
    """Files already imported AND indexed: both groups empty. An empty group is
    immediately done, so each stage's callback still fires once and finalizes its
    job."""
    engine, completed_events = immediate_db
    files = _make_files(tmp_path, 2)
    with _session(engine) as session:
        for f in files:
            session.add(MediaItem(full_file_path=f, status=PHOTO_STATUS_INDEXED))
        session.commit()

    with _session(engine) as session:
        result = enqueue_index_jobs(session, files)

    with _session(engine) as session:
        import_job = session.query(Job).filter_by(id=result.import_job_id).one()
        index_job = session.query(Job).filter_by(id=result.index_job_id).one()
        assert import_job.task_count == 0 and import_job.status == JOB_STATUS_COMPLETED
        assert index_job.task_count == 0 and index_job.status == JOB_STATUS_COMPLETED

    assert completed_events == ["import_photos", "index_photos"]
