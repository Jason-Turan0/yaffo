"""A lock named at enqueue time: one run per sync automation, not one per task."""
import pytest

from yaffo.taskq import TaskQueue

pytestmark = pytest.mark.unit


def test_enqueue_locked_stores_the_computed_lock_name(tmp_path):
    queue = TaskQueue(str(tmp_path / "q.db"))

    @queue.task(context=True)
    def sync_run(automation_id, task=None):
        return None

    result = sync_run.enqueue_locked("sync-7", 7)

    row = queue.store._conn().execute("SELECT name, args_json, lock_name, context FROM task WHERE id = ?",
                                      (result.id,)).fetchone()
    assert (row["name"], row["args_json"], row["lock_name"], row["context"]) == ("sync_run", "[7]", "sync-7", 1)


def test_a_held_lock_skips_the_second_task(tmp_path):
    queue = TaskQueue(str(tmp_path / "q.db"))
    store = queue.store
    assert store.try_acquire_lock("sync-7", "first")
    assert not store.try_acquire_lock("sync-7", "second")
    store.release_lock("sync-7")
    assert store.try_acquire_lock("sync-7", "second")
