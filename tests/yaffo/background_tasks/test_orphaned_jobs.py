"""The orphaned-job sweep: open Jobs that no unfinished queue work refers to are
finished the way their tasks would have finished them; Jobs that still have work
coming are left alone."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.tasks import complete_job, orphaned_jobs
from yaffo.background_tasks.tasks.orphaned_jobs import find_orphaned_jobs, sweep_orphaned_jobs
from yaffo.db import db
from yaffo.db.models import Job
from yaffo.taskq.store import Store
from yaffo.utils.time import utcnow

pytestmark = pytest.mark.unit

OLD = utcnow() - timedelta(hours=1)


class _Factory:
    def __init__(self, engine):
        self.engine = engine

    def __call__(self):
        return Session(self.engine)

    def remove(self):
        pass


@pytest.fixture
def engine(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    db.metadata.create_all(engine)
    monkeypatch.setattr(orphaned_jobs, "SessionFactory", _Factory(engine))
    monkeypatch.setattr(complete_job, "SessionFactory", _Factory(engine))
    yield engine
    engine.dispose()


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "queue.db"))
    monkeypatch.setattr(orphaned_jobs, "task_queue", type("Q", (), {"store": store})())
    monkeypatch.setattr(orphaned_jobs, "RECHECK_SECONDS", 0)
    return store


@pytest.fixture
def completed_events(monkeypatch):
    emitted = []
    monkeypatch.setattr(complete_job, "emit_job_completed_event", lambda session, job: emitted.append(job.id))
    return emitted


def _job(engine, job_id, status="RUNNING", updated_at=OLD, **fields):
    fields = {"task_count": 0, "completed_count": 0, "error_count": 0, "cancelled_count": 0, **fields}
    with Session(engine) as s:
        s.add(Job(id=job_id, name=fields.pop("name", "index_photos"), status=status,
                  created_at=updated_at, updated_at=updated_at, **fields))
        s.commit()


def _get(engine, job_id) -> Job:
    with Session(engine) as s:
        job = s.get(Job, job_id)
        s.expunge(job)
        return job


def _callback(job_id):
    return json.dumps({"name": "complete_job_callback", "args": [job_id], "kwargs": {}})


def _orphans(engine, store):
    with Session(engine) as s:
        return find_orphaned_jobs(s, store)


def test_a_job_whose_chord_finished_without_finishing_it_is_finalized(engine, store, completed_events):
    _job(engine, "index-1", task_count=20, completed_count=6, error_count=2)
    group = store.create_group(_callback("index-1"), None, total=1)
    member = store.insert_task("index_photo_task", ["index-1", ["a.jpg"]], {}, group_id=group)
    store.mark_done(member, None)
    store.add_group_result(group, None)
    callback = store.insert_task("complete_job_callback", ["index-1", [None]], {})
    store.mark_done(callback, None)  # its commit was lost; the queue thinks it's done

    sweep_orphaned_jobs.fn()

    job = _get(engine, "index-1")
    assert job.status == "COMPLETED" and job.error_count == 14 and job.completed_at is not None
    assert completed_events == ["index-1"]


def test_a_job_with_a_chord_still_running_is_left_alone(engine, store):
    _job(engine, "index-1")
    store.create_group(_callback("index-1"), None, total=2)

    assert _orphans(engine, store) == []


def test_a_job_named_by_a_ready_task_is_left_alone(engine, store):
    _job(engine, "dupes-1", name="find_duplicates", status="PENDING")
    store.insert_task("find_duplicates_task", [], {"job_id": "dupes-1", "file_paths": []})

    assert _orphans(engine, store) == []


def test_an_index_job_waiting_behind_its_import_chord_is_left_alone(engine, store):
    _job(engine, "import-1", name="import_photos", status="PENDING")
    _job(engine, "index-1", status="PENDING")
    continuation = [{"type": "single", "sig": {"name": "start_index_stage", "args": ["index-1"]}}]
    store.create_group(_callback("import-1"), json.dumps(continuation), total=3)

    assert _orphans(engine, store) == []


def test_a_recently_touched_job_is_left_alone(engine, store):
    _job(engine, "index-1", updated_at=utcnow())

    assert _orphans(engine, store) == []


def test_a_run_job_whose_task_ended_is_failed(engine, store, completed_events):
    task_id = store.insert_task("sync_run_task", [10], {}, context=True)
    _job(engine, task_id, name="from-peer")
    store.mark_done(task_id, None)

    sweep_orphaned_jobs.fn()

    job = _get(engine, task_id)
    assert job.status == "FAILED" and json.loads(job.job_data)["problem"] == "worker_stopped"
    assert completed_events == []


def test_a_run_job_whose_task_is_running_is_left_alone(engine, store):
    task_id = store.insert_task("sync_run_task", [10], {}, context=True)
    _job(engine, task_id, name="from-peer")
    store.mark_running(task_id)

    assert _orphans(engine, store) == []


def test_a_cancelled_job_that_never_stopped_gets_its_stop_time(engine, store):
    _job(engine, "index-1", status="CANCELLED", task_count=10, completed_count=4)

    sweep_orphaned_jobs.fn()

    job = _get(engine, "index-1")
    assert job.status == "CANCELLED" and job.completed_at is not None


def test_work_enqueued_between_the_two_looks_keeps_the_job(engine, store, monkeypatch):
    _job(engine, "index-1", status="PENDING")
    looks = iter([(set(), ""), (set(), '["index-1"]')])
    monkeypatch.setattr(store, "open_references", lambda: next(looks))

    assert _orphans(engine, store) == []


def test_finished_jobs_are_not_candidates(engine, store):
    _job(engine, "index-1", status="COMPLETED", completed_at=OLD)
    _job(engine, "index-2", status="FAILED", completed_at=OLD)

    assert _orphans(engine, store) == []
