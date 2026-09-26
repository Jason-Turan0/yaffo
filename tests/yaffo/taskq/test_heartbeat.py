"""The host's heartbeat and the store's read-only views used by diagnostics."""
import time

import pytest

from yaffo.taskq.host import Host
from yaffo.taskq.store import STATUS_ERROR, Store

pytestmark = pytest.mark.unit


def test_heartbeat_round_trip(tmp_path):
    store = Store(str(tmp_path / "q.db"))
    assert store.read_heartbeat() is None
    store.write_heartbeat(pid=7, started_at=100.0, workers=2, busy=1)
    store.write_heartbeat(pid=7, started_at=100.0, workers=2, busy=0)
    beat = store.read_heartbeat()
    assert (beat.pid, beat.started_at, beat.workers, beat.busy) == (7, 100.0, 2, 0)
    assert time.time() - beat.beat_at < 5


def test_host_beats_at_most_every_interval(tmp_path):
    host = Host(str(tmp_path / "q.db"), periodic=[], num_workers=0, max_tasks_per_worker=1)
    host._beat()
    first = host.store.read_heartbeat()
    host._beat()
    assert host.store.read_heartbeat().beat_at == first.beat_at
    assert first.workers == 0 and first.started_at == host.started_at


def test_failed_tasks_and_tasks_mentioning(tmp_path):
    store = Store(str(tmp_path / "q.db"))
    failed = store.insert_task("index_file", ["job-9", [1, 2]], {})
    store.mark_error(failed, "boom")
    store.insert_task("other", ["job-10"], {})
    assert [r["name"] for r in store.failed_tasks(time.time() - 60)] == ["index_file"]
    assert store.failed_tasks(time.time() - 60, name="other") == []
    assert [t["id"] for t in store.tasks_mentioning("job-9")] == [failed]
    assert store.status_counts(["index_file"]) == {STATUS_ERROR: 1}
    assert store.last_activity() is not None
