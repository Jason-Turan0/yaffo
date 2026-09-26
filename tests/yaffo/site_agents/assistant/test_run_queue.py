"""Why an assistant reply hasn't started: waiting for a busy worker, or no host."""
import time

import pytest

from yaffo.site_agents.assistant.run_queue import (
    QUEUE_HOST_STOPPED, QUEUE_WAITING, run_queue_status,
)
from yaffo.taskq import PRIORITY_INTERACTIVE
from yaffo.taskq.store import STATUS_DONE, Store

pytestmark = pytest.mark.unit


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "q.db"))


def _queue_reply(store, conversation_id=5):
    return store.insert_task("assistant_run_task", [conversation_id], {}, priority=PRIORITY_INTERACTIVE)


def _run(store, name, started_ago, now):
    task_id = store.insert_task(name, [], {})
    store._conn().execute("UPDATE task SET status='running', started_at=? WHERE id=?", (now - started_ago, task_id))


def _history(store, name, seconds, now):
    task_id = store.insert_task(name, [], {})
    store._conn().execute(
        "UPDATE task SET status=?, started_at=?, finished_at=? WHERE id=?",
        (STATUS_DONE, now - 100 - seconds, now - 100, task_id))


def test_nothing_to_say_when_the_reply_is_not_queued(app, store):
    store.write_heartbeat(pid=1, started_at=0, workers=2, busy=2)
    assert run_queue_status(store, 5) is None


def test_nothing_to_say_when_a_worker_is_free(app, store):
    _queue_reply(store)
    store.write_heartbeat(pid=1, started_at=0, workers=2, busy=1)
    assert run_queue_status(store, 5) is None


def test_host_not_running(app, store):
    _queue_reply(store)
    status = run_queue_status(store, 5)
    assert status.state == QUEUE_HOST_STOPPED
    assert "isn't running" in status.message

    store.write_heartbeat(pid=1, started_at=0, workers=2, busy=0)
    stale = run_queue_status(store, 5, now=time.time() + 120)
    assert stale.state == QUEUE_HOST_STOPPED


def test_waiting_behind_busy_workers_with_an_estimate(app, store):
    now = time.time()
    _history(store, "index_photo_task", 20, now)
    _run(store, "index_photo_task", 5, now)    # ~15s left
    _run(store, "index_photo_task", 12, now)   # ~8s left
    _queue_reply(store)
    store.write_heartbeat(pid=1, started_at=0, workers=2, busy=2)

    status = run_queue_status(store, 5, now=now)
    assert status.state == QUEUE_WAITING
    assert (status.ahead, status.workers, status.busy) == (0, 2, 2)
    assert status.busy_with == "index_photo_task"
    assert status.wait_seconds == 8
    assert "busy indexing photos" in status.message
    assert "Starts in about 8 seconds." in status.message


def test_waiting_with_tasks_ahead_and_no_history(app, store):
    now = time.time()
    _run(store, "export_photo_tag_task", 5, now)
    store.insert_task("generate_page_task", [1], {}, priority=PRIORITY_INTERACTIVE)
    _queue_reply(store)
    store.write_heartbeat(pid=1, started_at=0, workers=1, busy=1)

    status = run_queue_status(store, 5, now=now)
    assert status.ahead == 1
    assert status.wait_seconds is None
    assert "writing tags to photo files" in status.message
    assert "1 task is ahead of it." in status.message
    assert "Starts in" not in status.message
