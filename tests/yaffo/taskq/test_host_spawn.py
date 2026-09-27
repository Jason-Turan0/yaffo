"""End-to-end test of the host + spawn worker pool.

Spins up the real `Host` against a throwaway registry (no dlib) and a temp queue
db, then asserts: (1) enqueued tasks actually run in spawn-started children, and
(2) a child that hard-crashes mid-task (exit 139, the dlib SIGSEGV signature) is
contained -- the host records the failure, respawns the worker, and the remaining
work still completes. This is the property Huey couldn't give us.
"""
import sys
import time

import pytest

from yaffo.taskq.host import Host

pytestmark = [pytest.mark.integration, pytest.mark.slow]

# A standalone task module the spawn children import. Kept dlib-free and
# self-contained: it builds its own TaskQueue from env and records each task's
# effect into a separate sqlite file the test process can read back.
_PROBE = '''
import os, sqlite3
from yaffo.taskq import TaskQueue

tq = TaskQueue(filename=os.environ["TASKQ_DB"], immediate=False)

def _record(val):
    conn = sqlite3.connect(os.environ["TASKQ_OUT"], timeout=30)
    try:
        conn.execute("INSERT INTO out(val) VALUES (?)", (val,))
        conn.commit()
    finally:
        conn.close()

@tq.task()
def marker(name):
    _record(name)

@tq.task()
def boom():
    os._exit(139)  # simulate a native segfault

@tq.task()
def bad_return():
    return {1, 2, 3}  # a set: not JSON-serializable

@tq.task(context=True)
def raise_mid_run(task=None):
    # Open a run Job keyed by this task's id, then raise outside any handling.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from yaffo.db.repositories.job_repository import open_run_job
    engine = create_engine("sqlite:///" + os.environ["TASKQ_APP_DB"])
    with Session(engine) as session:
        open_run_job(session, task.id, name="probe_run")
    raise RuntimeError("database is locked")

@tq.task(context=True)
def crash_mid_run(task=None):
    # Open a run Job keyed by this task's id, as the automation tasks do, then die
    # the way a native crash in the ML code would.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from yaffo.db.repositories.job_repository import open_run_job
    engine = create_engine("sqlite:///" + os.environ["TASKQ_APP_DB"])
    with Session(engine) as session:
        open_run_job(session, task.id, name="probe_run")
    os._exit(139)
'''


@pytest.fixture
def probe(tmp_path, monkeypatch):
    # The host primes the API key into env before each spawn, which otherwise
    # reads the OS keychain and pops a password prompt. Stub it out: this test is
    # about worker supervision, not key plumbing.
    monkeypatch.setattr("yaffo.taskq.host.llm_config.prime_subprocess_env", lambda: None)

    db = tmp_path / "queue.db"
    out = tmp_path / "out.db"
    import sqlite3
    conn = sqlite3.connect(out)
    conn.execute("CREATE TABLE out (val TEXT)")
    conn.commit()
    conn.close()

    (tmp_path / "taskq_probe.py").write_text(_PROBE)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("TASKQ_DB", str(db))
    monkeypatch.setenv("TASKQ_OUT", str(out))
    app_db = tmp_path / "app.db"
    from sqlalchemy import create_engine
    from yaffo.db import db as app_models
    app_engine = create_engine(f"sqlite:///{app_db}")
    app_models.metadata.create_all(app_engine)
    app_engine.dispose()
    monkeypatch.setenv("TASKQ_APP_DB", str(app_db))

    # drop any cached import so the test process picks up the env-bound module
    sys.modules.pop("taskq_probe", None)
    import importlib
    mod = importlib.import_module("taskq_probe")
    yield mod, db, out
    sys.modules.pop("taskq_probe", None)


def _recorded(out) -> set[str]:
    import sqlite3
    conn = sqlite3.connect(out)
    try:
        return {r[0] for r in conn.execute("SELECT val FROM out")}
    finally:
        conn.close()


def _run_until(host, predicate, timeout=20.0):
    host.setup()
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            host.run_once()
            if predicate():
                return True
            time.sleep(0.05)
        return False
    finally:
        host.shutdown()


def _host(db, on_task_failed=None):
    return Host(
        on_task_failed=on_task_failed,
        filename=str(db),
        periodic=[],
        num_workers=2,
        max_tasks_per_worker=100,
        bootstrap="taskq_probe",
        queue_ref="taskq_probe:tq",
    )


def test_tasks_run_in_spawn_workers(probe):
    mod, db, out = probe
    expected = {"a", "b", "c", "d", "e"}
    for name in expected:
        mod.marker(name)

    host = _host(db)
    assert _run_until(host, lambda: _recorded(out) >= expected), _recorded(out)
    assert _recorded(out) == expected


def test_worker_crash_is_isolated_and_recovered(probe):
    mod, db, out = probe
    mod.marker("before")
    mod.boom()          # kills whichever worker picks it up
    mod.marker("after1")
    mod.marker("after2")

    host = _host(db)
    survivors = {"before", "after1", "after2"}
    ok = _run_until(host, lambda: _recorded(out) >= survivors)
    assert ok, _recorded(out)
    # the crash did not take down the host, and the pool was kept at strength
    assert len([w for w in host.workers.values()]) == 2


def _task_status(db, task_id):
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT status FROM task WHERE id=?", (task_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_non_json_return_is_a_clean_failure(probe):
    mod, db, out = probe
    res = mod.bad_return()   # worker rejects the non-JSON return -> task error
    mod.marker("after")

    host = _host(db)
    # Wait until the failure is actually recorded -- "after" (run by the other worker)
    # can land before bad_return is marked error, so the predicate must check both.
    ok = _run_until(host, lambda: _recorded(out) >= {"after"} and _task_status(db, res.id) == "error")
    assert ok, (_recorded(out), _task_status(db, res.id))

    assert _task_status(db, res.id) == "error"  # reported as a failure, not a host crash
    assert len(host.workers) == 2               # host survived, pool intact


def test_a_worker_crash_does_not_leave_its_run_job_running(probe, tmp_path):
    """Scenario 32: a worker that dies mid-run (host still up) has its task marked
    error and never retried -- not now, not on the next host start. The host's
    failure hook fails its run Job, so it isn't left RUNNING forever."""
    mod, db, out = probe
    res = mod.crash_mid_run()

    host = _host(db, on_task_failed=_app_db_hook(tmp_path))
    assert _run_until(host, lambda: _task_status(db, res.id) == "error"), _task_status(db, res.id)
    assert _host(db).store.requeue_running() == 0  # a later host start doesn't retry it either

    assert _job(tmp_path, res.id) == ("FAILED", "worker crashed (exitcode=139)")


def test_a_task_that_raises_does_not_leave_its_run_job_running(probe, tmp_path):
    """The other way a task errors without finishing its Job: an exception its own
    code didn't handle (e.g. a locked database outside record_run's try)."""
    mod, db, out = probe
    res = mod.raise_mid_run()

    host = _host(db, on_task_failed=_app_db_hook(tmp_path))
    assert _run_until(host, lambda: (_job(tmp_path, res.id) or (None,))[0] == "FAILED"), _job(tmp_path, res.id)

    assert _job(tmp_path, res.id) == ("FAILED", "RuntimeError: database is locked")


def test_a_failing_hook_does_not_take_the_host_down(probe):
    mod, db, out = probe
    res = mod.boom()
    mod.marker("after")

    def broken_hook(task_id, name, error):
        raise RuntimeError("hook bug")

    host = _host(db, on_task_failed=broken_hook)
    ok = _run_until(host, lambda: _recorded(out) >= {"after"} and _task_status(db, res.id) == "error")
    assert ok, (_recorded(out), _task_status(db, res.id))


def _app_db_hook(tmp_path):
    """yaffo's failure hook, bound to the probe's app db instead of the real one."""
    from functools import partial
    from sqlalchemy import create_engine
    from yaffo.background_tasks.task_failures import record_task_failure
    return partial(record_task_failure, create_engine(f"sqlite:///{tmp_path / 'app.db'}"))


def _job(tmp_path, job_id):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "app.db")
    try:
        return conn.execute("SELECT status, error FROM jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        conn.close()
