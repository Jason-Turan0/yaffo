"""Persistent-path coordinator spec.

Drives the same store + coordinator functions the host uses, but single-process and
synchronous (no spawn workers), so chord barriers, pipeline continuations, the
positional result-append contract, named locks, context ids, delayed eta and crash
recovery are all asserted deterministically. The immediate-mode equivalent of the
real import->index pipeline lives in tests/yaffo/utils/test_index_jobs.py.
"""
import time

import pytest

from yaffo.taskq import TaskQueue, chord
from yaffo.taskq.core import _NO_PREV, enqueue_pipeline_rows, on_task_finished
from yaffo.taskq.signatures import Pipeline, Signature, SingleLink
from yaffo.taskq.store import STATUS_SKIPPED, Store

pytestmark = pytest.mark.unit


@pytest.fixture
def queue(tmp_path):
    return TaskQueue(filename=str(tmp_path / "queue.db"), immediate=False)


def drain(store: Store, results: dict[str, object] | None = None) -> list[tuple]:
    """Emulate the host single-threaded: claim every ready task, 'run' it
    (returning results[name] or None), honour locks, and advance composition.
    Returns the ordered list of (name, args) actually executed."""
    results = results or {}
    executed: list[tuple] = []
    held: set[str] = set()
    for _ in range(1000):  # guard against an accidental infinite graph
        rows = store.fetch_ready(time.time(), limit=100)
        if not rows:
            break
        progressed = False
        for row in rows:
            if row.lock_name:
                if row.lock_name in held or not store.try_acquire_lock(row.lock_name, row.id):
                    store.mark_skipped(row.id)
                    progressed = True
                    continue
                held.add(row.lock_name)
            store.mark_running(row.id)
            executed.append((row.name, tuple(row.args)))
            result = results.get(row.name)
            store.mark_done(row.id, result)
            on_task_finished(store, row, result)
            if row.lock_name:
                store.release_lock(row.lock_name)
                held.discard(row.lock_name)
            progressed = True
        if not progressed:
            break
    return executed


def test_single_task_enqueue(queue):
    @queue.task()
    def solo(x):
        return None

    queue.enqueue(Pipeline([SingleLink(solo.s(5))]))
    assert drain(queue.store) == [("solo", (5,))]


def test_chord_callback_fires_once_after_all_members(queue):
    @queue.task()
    def member(i):
        return i

    @queue.task()
    def callback(job_id, results=None):
        return None

    queue.enqueue(chord([member.s(1), member.s(2), member.s(3)], callback.s("job")))
    executed = drain(queue.store, results={"member": 7})

    names = [n for n, _ in executed]
    assert names.count("member") == 3
    assert names.count("callback") == 1
    # callback runs last and receives the list of member results appended
    cb_name, cb_args = executed[-1]
    assert cb_name == "callback"
    assert cb_args == ("job", [7, 7, 7])


def test_pipeline_appends_prev_result(queue):
    @queue.task()
    def first(x):
        return None

    @queue.task()
    def second(x, prev=None):
        return None

    queue.enqueue(first.s("a").then(second, "b"))
    executed = drain(queue.store, results={"first": "RESULT"})
    assert executed == [("first", ("a",)), ("second", ("b", "RESULT"))]


def test_chord_then_pipeline(queue):
    """chord(members, cb).then(next, arg) -- the index-jobs shape: members, then
    the callback, then the continuation with the callback's result appended."""
    @queue.task()
    def member(i):
        return None

    @queue.task()
    def callback(job_id, results=None):
        return "CB"

    @queue.task()
    def nxt(arg, prev=None):
        return None

    queue.enqueue(chord([member.s(1), member.s(2)], callback.s("imp")).then(nxt, "idx"))
    executed = drain(queue.store, results={"callback": "CB"})

    assert executed[-1] == ("nxt", ("idx", "CB"))
    assert [n for n, _ in executed].count("member") == 2


def test_lock_task_skips_when_held(queue):
    """Two tasks sharing a lock: only one runs; the other is skipped (Huey's
    lock_task semantics for file-sync pile-ups)."""
    sig = Signature(task_name="locked", lock_name="the-lock")

    # Pre-hold the lock, then enqueue one locked task -> it must be skipped.
    queue.store.try_acquire_lock("the-lock", "someone-else")
    enqueue_pipeline_rows(queue.store, [SingleLink(sig)], _NO_PREV)
    executed = drain(queue.store)

    assert executed == []  # skipped because lock held
    row = queue.store.fetch_ready(time.time(), 10)
    assert row == []  # nothing left ready
    # confirm it landed in skipped status
    cur = queue.store._conn().execute("SELECT status FROM task").fetchone()
    assert cur["status"] == STATUS_SKIPPED


def test_context_flag_persisted(queue):
    @queue.task(context=True)
    def ctx_task(job_id, task=None):
        return None

    queue.enqueue(Pipeline([SingleLink(ctx_task.s("job"))]))
    rows = queue.store.fetch_ready(time.time(), 10)
    assert len(rows) == 1
    assert rows[0].context is True


def test_delayed_task_not_ready_until_eta(queue):
    @queue.task()
    def later(x):
        return None

    later.schedule(args=("z",), delay=60)
    assert queue.store.fetch_ready(time.time(), 10) == []
    assert len(queue.store.fetch_ready(time.time() + 61, 10)) == 1


def test_periodic_single_fire_per_minute(queue):
    assert queue.store.claim_periodic_minute("tick", 100) is True
    assert queue.store.claim_periodic_minute("tick", 100) is False  # same minute
    assert queue.store.claim_periodic_minute("tick", 101) is True   # next minute


def test_requeue_running_on_startup(queue):
    @queue.task()
    def t():
        return None

    queue.enqueue(Pipeline([SingleLink(t.s())]))
    row = queue.store.fetch_ready(time.time(), 10)[0]
    queue.store.mark_running(row.id)
    assert queue.store.fetch_ready(time.time(), 10) == []  # running, not ready

    assert queue.store.requeue_running() == 1
    assert len(queue.store.fetch_ready(time.time(), 10)) == 1  # back to ready
