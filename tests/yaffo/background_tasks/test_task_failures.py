"""The task-queue host's failure hook (background_tasks/task_failures): a task that
errored without finishing its run Job gets that Job ended, with the useful line of
the error kept."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.task_failures import MAX_ERROR_CHARS, _summary, record_task_failure
from yaffo.db import db
from yaffo.db.models import Job

pytestmark = pytest.mark.unit


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    db.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_a_failed_tasks_run_job_is_failed_with_the_last_traceback_line(engine):
    with Session(engine) as s:
        s.add(Job(id="task-1", name="classify_labels", status="RUNNING"))
        s.commit()
    traceback = 'Traceback (most recent call last):\n  File "x.py", line 1\nsqlite3.OperationalError: database is locked\n'

    record_task_failure(engine, "task-1", "classify_labels_automation_task", traceback)

    with Session(engine) as s:
        job = s.get(Job, "task-1")
        assert job.status == "FAILED" and job.error == "sqlite3.OperationalError: database is locked"
        assert job.job_data == '{"problem": "task_error"}'


def test_a_worker_crash_records_the_worker_stopped_problem(engine):
    with Session(engine) as s:
        s.add(Job(id="task-1", name="classify_labels", status="RUNNING"))
        s.commit()

    record_task_failure(engine, "task-1", "classify_labels_automation_task", "worker crashed (exitcode=139)")

    with Session(engine) as s:
        assert s.get(Job, "task-1").job_data == '{"problem": "worker_stopped"}'


def test_a_task_without_a_run_job_changes_nothing(engine):
    record_task_failure(engine, "batch-task", "index_photo_task", "worker crashed (exitcode=139)")

    with Session(engine) as s:
        assert s.query(Job).count() == 0


def test_summary_keeps_the_last_line_and_caps_it():
    assert _summary("worker crashed (exitcode=139)") == "worker crashed (exitcode=139)"
    assert _summary("") == "The task failed"
    assert len(_summary("x" * 5000)) == MAX_ERROR_CHARS
