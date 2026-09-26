"""Dispatch priority: interactive work (and the scheduler tick) is handed out ahead
of queued bulk work, and a waiting task can find its place in line."""
import sqlite3
import time

import pytest

from yaffo.taskq import PRIORITY_INTERACTIVE, PRIORITY_NORMAL, TaskQueue, chord, crontab
from yaffo.taskq.host import Host
from yaffo.taskq.signatures import Signature
from yaffo.taskq.store import STATUS_DONE, Store

pytestmark = pytest.mark.unit


def _priorities(store: Store) -> dict[str, int]:
    rows = store._conn().execute("SELECT name, priority FROM task").fetchall()
    return {r["name"]: r["priority"] for r in rows}


def test_ready_tasks_dispatch_by_priority_then_age(tmp_path):
    store = Store(str(tmp_path / "q.db"))
    bulk = [store.insert_task(f"index_{i}", [], {}) for i in range(3)]
    reply = store.insert_task("assistant_run_task", [1], {}, priority=PRIORITY_INTERACTIVE)
    assert [r.id for r in store.fetch_ready(time.time(), limit=4)] == [reply, *bulk]


def test_existing_queue_db_gains_the_priority_column(tmp_path):
    path = tmp_path / "q.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE task (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, args_json TEXT NOT NULL DEFAULT '[]',
        kwargs_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'ready',
        eta REAL, lock_name TEXT, context INTEGER NOT NULL DEFAULT 0, group_id TEXT,
        continuation_json TEXT, result_json TEXT, error TEXT, created_at REAL NOT NULL,
        started_at REAL, finished_at REAL, attempts INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("INSERT INTO task (id, name, created_at) VALUES ('old', 'index', 1.0)")
    conn.commit()
    conn.close()

    store = Store(str(path))
    Store(str(path))  # a second process opening it is fine too
    assert _priorities(store) == {"index": PRIORITY_NORMAL}
    assert [r.id for r in store.fetch_ready(time.time(), limit=1)] == ["old"]


def test_signature_round_trips_priority_and_defaults_for_old_continuations():
    sig = Signature("t", (1,), {}, priority=PRIORITY_INTERACTIVE)
    assert Signature.from_dict(sig.to_dict()).priority == PRIORITY_INTERACTIVE
    assert Signature.from_dict({"name": "t"}).priority == PRIORITY_NORMAL


def test_task_priority_reaches_chord_members_and_callback_rows(tmp_path):
    queue = TaskQueue(str(tmp_path / "q.db"))

    @queue.task(priority=PRIORITY_INTERACTIVE)
    def member(x):
        return x

    @queue.task()
    def callback(results):
        return results

    queue.enqueue(chord([member.s(1)], callback.s()))
    assert _priorities(queue.store) == {"member": PRIORITY_INTERACTIVE}


def test_periodic_ticks_are_interactive(tmp_path):
    host = Host(str(tmp_path / "q.db"), periodic=[("tick", crontab(minute="*"))],
                num_workers=0, max_tasks_per_worker=1)
    host._tick_periodic()
    assert _priorities(host.store) == {"tick": PRIORITY_INTERACTIVE}


@pytest.mark.parametrize("module,name", [
    ("assistant_run", "assistant_run_task"),
    ("generate_page", "generate_page_task"),
    ("generate_automation", "generate_automation_task"),
    ("generate_theme", "generate_theme_task"),
    ("assign_faces_to_person", "assign_faces_to_person"),
    ("dispatcher", "dispatch_scheduled_tasks"),
])
def test_user_facing_tasks_are_interactive(module, name):
    import importlib
    importlib.import_module(f"yaffo.background_tasks.tasks.{module}")
    from yaffo.background_tasks.config import task_queue
    assert task_queue.registry[name].priority == PRIORITY_INTERACTIVE


def test_bulk_tasks_stay_normal():
    import importlib
    importlib.import_module("yaffo.background_tasks.tasks.index_photo")
    from yaffo.background_tasks.config import task_queue
    assert task_queue.registry["index_photo_task"].priority == PRIORITY_NORMAL


def test_queue_wait_counts_what_goes_first(tmp_path):
    store = Store(str(tmp_path / "q.db"))
    running = store.insert_task("index_photo_task", [], {})
    store.mark_running(running)
    store.insert_task("index_photo_task", [], {})  # bulk: behind an interactive task
    store.insert_task("generate_page_task", [7], {}, priority=PRIORITY_INTERACTIVE)
    store.insert_task("assistant_run_task", [3], {}, priority=PRIORITY_INTERACTIVE)
    store.insert_task("assistant_run_task", [4], {}, priority=PRIORITY_INTERACTIVE, eta=time.time() + 60)

    wait = store.queue_wait("assistant_run_task", [3])
    assert wait.ahead == 1  # the older interactive task; not bulk, not the later one
    assert [name for name, _ in wait.running] == ["index_photo_task"]
    assert store.queue_wait("assistant_run_task", [99]) is None


def test_average_durations_uses_recent_successful_runs(tmp_path):
    store = Store(str(tmp_path / "q.db"))
    now = time.time()
    for seconds in (10, 20):
        task_id = store.insert_task("index_photo_task", [], {})
        store._conn().execute(
            "UPDATE task SET status=?, started_at=?, finished_at=? WHERE id=?",
            (STATUS_DONE, now - seconds, now, task_id))
    assert store.average_durations(["index_photo_task", "other"], now - 60) == {"index_photo_task": 15}
    assert store.average_durations([], now) == {}
