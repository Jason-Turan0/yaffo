"""Unit tests for the scheduled duplicate-scan system automation: _open_scan_job
creates a find_duplicates Job (tagged with the automation) over all indexed photos,
and the handler enqueues the task with the automation id. The DB session and
repository are stubbed so the test stays fast and focuses on the wiring (Job shape),
not perceptual hashing.
"""
from types import SimpleNamespace

import pytest

from yaffo.background_tasks.tasks import duplicate_scan as mod
from yaffo.db.models import JOB_STATUS_PENDING

pytestmark = pytest.mark.unit


class _FakeSession:
    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True


def test_open_scan_job_creates_tagged_job(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(mod.media_repository, "get_all_media_item_paths", lambda s: ["/a.jpg", "/b.jpg"])

    opened = mod._open_scan_job(session, automation_id=7)

    assert opened is not None
    job_id, file_paths = opened
    assert file_paths == ["/a.jpg", "/b.jpg"]
    assert len(session.added) == 1
    job = session.added[0]
    assert job.id == job_id
    assert job.name == "find_duplicates"
    assert job.status == JOB_STATUS_PENDING
    assert job.task_count == 2
    assert job.automation_id == 7
    assert session.committed is True


def test_open_scan_job_noop_when_no_photos(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(mod.media_repository, "get_all_media_item_paths", lambda s: [])

    assert mod._open_scan_job(session, automation_id=7) is None
    assert session.added == []


def test_empty_automation_scan_records_run(monkeypatch):
    automation = SimpleNamespace(id=7)
    calls = []

    class FakeFactory:
        def __call__(self):
            return SimpleNamespace(get=lambda model, automation_id: automation, close=lambda: None)

        def remove(self):
            pass

    monkeypatch.setattr(mod, "SessionFactory", FakeFactory())
    monkeypatch.setattr(mod, "_open_scan_job", lambda session, automation_id, ids: None)
    monkeypatch.setattr(mod, "record_run", lambda session, target, work, media_item_ids: calls.append((target, media_item_ids)))
    monkeypatch.setattr(mod, "find_duplicates_task", lambda **kwargs: pytest.fail("empty scan enqueued hashing"))

    mod.duplicate_scan_task.fn(automation_id=7, media_item_ids=[])

    assert calls == [(automation, [])]


def test_handler_enqueues_with_automation_id(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "duplicate_scan_task", lambda automation_id: calls.append(automation_id))
    mod.enqueue_duplicate_scan(SimpleNamespace(id=42), context=None)
    assert calls == [42]


def test_scheduled_duplicate_scan_uses_selected_ids(monkeypatch):
    from yaffo.background_tasks.events import EventContext

    calls = []
    monkeypatch.setattr(mod, "duplicate_scan_task", lambda automation_id, media_item_ids: calls.append((automation_id, media_item_ids)))
    mod.enqueue_duplicate_scan(SimpleNamespace(id=42), EventContext(event_type=None, media_item_ids=[3, 7]))
    assert calls == [(42, [3, 7])]
