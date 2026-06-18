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
'''


@pytest.fixture
def probe(tmp_path, monkeypatch):
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


def _host(db):
    return Host(
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


def test_non_json_return_is_a_clean_failure(probe):
    import sqlite3
    mod, db, out = probe
    res = mod.bad_return()   # worker rejects the non-JSON return -> task error
    mod.marker("after")

    host = _host(db)
    ok = _run_until(host, lambda: _recorded(out) >= {"after"})
    assert ok, _recorded(out)

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM task WHERE id=?", (res.id,)).fetchone()[0]
    conn.close()
    assert status == "error"          # reported as a failure, not a host crash
    assert len(host.workers) == 2     # host survived, pool intact
