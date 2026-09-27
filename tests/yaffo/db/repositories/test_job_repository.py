"""Unit tests for job progress bookkeeping (db/repositories/job_repository): the
completion estimate, the earliest-start stamp chord batches share, and the
per-tick refresh that reads back bulk-incremented counts."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.progress_reporter import ProgressReporter
from yaffo.db import db
from yaffo.db.models import Job, JOB_STATUS_CANCELLED, JOB_STATUS_COMPLETED, JOB_STATUS_RUNNING
from yaffo.db.repositories import job_repository
from yaffo.db.repositories.job_repository import (
    earliest_started_at,
    estimate_completion,
    fail_run_job,
    is_job_cancelled,
    open_run_job,
    refresh_estimated_completion,
    run_job_id,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 27, 12, 0, 0)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


def _job(session, **kw):
    defaults = dict(id="j", name="index_photos", status=JOB_STATUS_RUNNING, task_count=0,
                    completed_count=0, error_count=0, cancelled_count=0)
    defaults.update(kw)
    session.add(Job(**defaults))
    session.commit()


def test_estimate_projects_the_rate_so_far_over_what_is_left():
    # 25 of 100 done in 10 minutes -> 75 left at 2.5/min -> 30 more minutes
    assert estimate_completion(NOW - timedelta(minutes=10), 100, 20, 5, 0, NOW) == NOW + timedelta(minutes=30)


def test_estimate_leaves_cancelled_items_out_of_both_sides():
    # 10 done in 10 minutes, 40 cancelled: 50 left at 1/min
    assert estimate_completion(NOW - timedelta(minutes=10), 100, 10, 0, 40, NOW) == NOW + timedelta(minutes=50)


@pytest.mark.parametrize("started_at, task_count, done", [
    (None, 100, 10),   # no start to measure from
    (NOW, 0, 0),       # nothing to do
    (NOW, 100, 0),     # no rate yet
])
def test_estimate_is_none_without_a_rate(started_at, task_count, done):
    assert estimate_completion(started_at, task_count, done, 0, 0, NOW) is None


def test_estimate_of_a_finished_job_is_now():
    assert estimate_completion(NOW - timedelta(hours=1), 10, 8, 2, 0, NOW) == NOW


def test_earliest_started_at_keeps_the_first_batch_start(session):
    _job(session)
    first, later = NOW - timedelta(minutes=5), NOW
    for started in (later, first, later):
        session.query(Job).filter_by(id="j").update({"started_at": earliest_started_at(started)})
        session.commit()
    assert session.get(Job, "j").started_at == first


def test_refresh_reads_back_bulk_incremented_counts(session, monkeypatch):
    monkeypatch.setattr(job_repository, "utcnow", lambda: NOW)
    _job(session, task_count=100, started_at=NOW - timedelta(minutes=10))

    session.query(Job).filter_by(id="j").update({"completed_count": Job.completed_count + 50})
    refresh_estimated_completion(session, "j")
    session.commit()

    job = session.get(Job, "j")
    session.refresh(job)
    assert job.estimated_completed_at == NOW + timedelta(minutes=10)


def test_progress_reporter_refreshes_the_estimate(session, monkeypatch):
    monkeypatch.setattr("yaffo.background_tasks.progress_reporter.utcnow", lambda: NOW)
    _job(session, started_at=NOW - timedelta(minutes=4))

    ProgressReporter(session, "j").progress_update(10, 2, 0, 0)

    assert session.get(Job, "j").estimated_completed_at == NOW + timedelta(minutes=16)


def test_is_job_cancelled_sees_a_cancel_and_a_deleted_job(session):
    _job(session)
    assert is_job_cancelled(session, "j") is False
    session.get(Job, "j").status = JOB_STATUS_CANCELLED
    session.commit()
    assert is_job_cancelled(session, "j") is True
    assert is_job_cancelled(session, "missing") is True


def test_open_run_job_creates_a_running_job(session):
    job = open_run_job(session, "task-1", name="classify_labels", task_count=1)

    assert job.id == "task-1" and job.status == JOB_STATUS_RUNNING
    assert job.name == "classify_labels" and job.task_count == 1 and job.started_at is not None


def test_a_retry_adopts_the_stranded_job_with_fresh_counts(session):
    _job(session, id="task-1", task_count=100, completed_count=40, error_count=2,
         started_at=NOW - timedelta(hours=1), estimated_completed_at=NOW)

    job = open_run_job(session, "task-1", name="ignored on adopt")

    assert session.query(Job).count() == 1  # no second Job
    assert job.name == "index_photos" and job.status == JOB_STATUS_RUNNING
    assert (job.task_count, job.completed_count, job.error_count) == (0, 0, 0)
    assert job.started_at > NOW - timedelta(hours=1) and job.estimated_completed_at is None


def test_a_retry_of_a_cancelled_run_marks_it_stopped_and_does_not_run(session):
    _job(session, id="task-1", status=JOB_STATUS_CANCELLED)

    assert open_run_job(session, "task-1", name="x") is None
    job = session.get(Job, "task-1")
    session.refresh(job)
    assert job.status == JOB_STATUS_CANCELLED and job.completed_at is not None


def test_a_retry_of_a_finished_run_does_not_run(session):
    _job(session, id="task-1", status=JOB_STATUS_COMPLETED, completed_count=5)

    assert open_run_job(session, "task-1", name="x") is None
    assert session.get(Job, "task-1").completed_count == 5


def test_run_job_id_is_the_queue_task_id():
    from types import SimpleNamespace
    assert run_job_id(SimpleNamespace(id="task-9")) == "task-9"
    assert run_job_id(None) != run_job_id(None)  # called without a task: a fresh id each time


def test_a_start_in_the_future_estimates_now_not_the_past():
    """Scenario 48: a clock change can leave started_at after now. Elapsed time is
    clamped to zero, so the estimate is now, never a time already gone."""
    assert estimate_completion(NOW + timedelta(minutes=10), 100, 10, 0, 0, NOW) == NOW


def test_fail_run_job_fails_a_running_job(session):
    _job(session, id="task-1", estimated_completed_at=NOW)

    assert fail_run_job(session, "task-1", "worker crashed (exitcode=139)", "worker_stopped") is True
    job = session.get(Job, "task-1")
    assert job.status == "FAILED" and job.error == "worker crashed (exitcode=139)"
    assert job.job_data == '{"problem": "worker_stopped"}'
    assert job.completed_at is not None and job.estimated_completed_at is None


def test_fail_run_job_stops_a_cancelled_job_without_failing_it(session):
    _job(session, id="task-1", status=JOB_STATUS_CANCELLED)

    assert fail_run_job(session, "task-1", "boom", "task_error") is True
    job = session.get(Job, "task-1")
    assert job.status == JOB_STATUS_CANCELLED and job.completed_at is not None and job.error is None


@pytest.mark.parametrize("status", [JOB_STATUS_COMPLETED, "FAILED"])
def test_fail_run_job_leaves_an_ended_job_alone(session, status):
    _job(session, id="task-1", status=status)

    assert fail_run_job(session, "task-1", "boom", "task_error") is False
    assert session.get(Job, "task-1").status == status


def test_fail_run_job_ignores_a_task_without_a_job(session):
    assert fail_run_job(session, "not-a-job", "boom", "task_error") is False
