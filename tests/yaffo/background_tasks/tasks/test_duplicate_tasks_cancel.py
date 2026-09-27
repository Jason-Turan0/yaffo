"""Cancellation endings for the duplicate tasks and the chord finalizer: a cancelled
job stays CANCELLED (never overwritten to COMPLETED) and gets completed_at once its
work has ended, which is what turns "Stopping" into "Cancelled"."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.tasks import complete_job
from yaffo.background_tasks.tasks import find_duplicates as find_mod
from yaffo.background_tasks.tasks import remove_duplicates as remove_mod
from yaffo.background_tasks.utils import SessionFactory, engine as prod_engine
from yaffo.db import db
from yaffo.db.models import Job, JOB_STATUS_CANCELLED, JOB_STATUS_RUNNING

pytestmark = pytest.mark.unit


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    db.metadata.create_all(engine)
    SessionFactory.configure(bind=engine)
    task_queue.immediate = True
    try:
        yield engine
    finally:
        task_queue.immediate = False
        SessionFactory.remove()
        SessionFactory.configure(bind=prod_engine)
        engine.dispose()


def _job(engine, status, task_count=0):
    with Session(engine) as s:
        s.add(Job(id="j", name="remove_duplicates", status=status, task_count=task_count,
                  completed_count=0, error_count=0, cancelled_count=0))
        s.commit()


def _read(engine) -> Job:
    with Session(engine) as s:
        job = s.get(Job, "j")
        s.expunge(job)
        return job


def test_remove_cancelled_mid_run_stays_cancelled_and_stops(engine, tmp_path, monkeypatch):
    _job(engine, JOB_STATUS_RUNNING, task_count=25)
    statuses = iter([JOB_STATUS_RUNNING, JOB_STATUS_RUNNING, JOB_STATUS_CANCELLED])

    def cancel_on_second_check(job_id):
        status = next(statuses)
        if status == JOB_STATUS_CANCELLED:
            with Session(engine) as s:
                s.get(Job, "j").status = JOB_STATUS_CANCELLED
                s.commit()
        return status
    monkeypatch.setattr(remove_mod, "get_job_status", cancel_on_second_check)

    remove_mod.remove_duplicates_task("j", [str(tmp_path / f"gone{i}.jpg") for i in range(25)], "trash")

    job = _read(engine)
    assert job.status == JOB_STATUS_CANCELLED
    assert job.cancelled_count == 5 and job.completed_at is not None  # start check, 10 ok, 20 cancelled


def test_a_cancel_after_the_last_check_is_not_overwritten(engine, tmp_path, monkeypatch):
    """The final write decides COMPLETED vs CANCELLED in the UPDATE, so a cancel that
    lands after the task's last check survives."""
    _job(engine, JOB_STATUS_CANCELLED, task_count=2)
    monkeypatch.setattr(remove_mod, "get_job_status", lambda job_id: JOB_STATUS_RUNNING)

    remove_mod.remove_duplicates_task("j", [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")], "trash")

    job = _read(engine)
    assert job.status == JOB_STATUS_CANCELLED and job.completed_at is not None


@pytest.mark.parametrize("task", [
    lambda: find_mod.find_duplicates_task("j", ["/a.jpg"]),
    lambda: remove_mod.remove_duplicates_task("j", ["/a.jpg"], "trash"),
])
def test_a_task_cancelled_before_it_starts_marks_the_job_stopped(engine, task):
    _job(engine, JOB_STATUS_CANCELLED)

    task()

    job = _read(engine)
    assert job.status == JOB_STATUS_CANCELLED and job.completed_at is not None


def test_finalize_stamps_a_cancelled_chord_job_without_completing_it(engine):
    _job(engine, JOB_STATUS_CANCELLED)

    complete_job.finalize_job("j")

    job = _read(engine)
    assert job.status == JOB_STATUS_CANCELLED and job.completed_at is not None


def test_a_retried_duplicate_scan_does_not_scan_again(engine, monkeypatch):
    from types import SimpleNamespace
    from yaffo.background_tasks.tasks import duplicate_scan
    with Session(engine) as s:
        s.add(Job(id="task-1", name="find_duplicates", status=JOB_STATUS_RUNNING, task_count=3))
        s.commit()
    monkeypatch.setattr(duplicate_scan, "find_duplicates_task", lambda **kw: pytest.fail("scanned twice"))

    duplicate_scan.duplicate_scan_task.fn(automation_id=None, task=SimpleNamespace(id="task-1"))

    with Session(engine) as s:
        assert s.query(Job).count() == 1


def test_find_duplicates_records_start_and_estimate(engine, monkeypatch):
    """Scenario 47 (find): the scan stamps started_at and ticks the estimate, which
    lands on its finish time once every file is hashed."""
    from types import SimpleNamespace
    _job(engine, "PENDING", task_count=60)
    monkeypatch.setattr(find_mod, "_media_hash", lambda path, posters, temp_dir: path[-1])
    monkeypatch.setattr(find_mod, "_get_indexed_video_posters", lambda paths: {})

    find_mod.find_duplicates_task.fn("j", [f"/p/{i}.jpg" for i in range(60)], task=SimpleNamespace(id="t"))

    job = _read(engine)
    assert job.started_at is not None
    assert job.estimated_completed_at is not None and job.estimated_completed_at >= job.started_at


def test_remove_duplicates_records_start_and_estimate(engine, tmp_path):
    """Scenario 47 (remove)."""
    _job(engine, "PENDING", task_count=3)

    remove_mod.remove_duplicates_task("j", [str(tmp_path / f"gone{i}.jpg") for i in range(3)], "trash")

    job = _read(engine)
    assert job.started_at is not None and job.estimated_completed_at is not None
