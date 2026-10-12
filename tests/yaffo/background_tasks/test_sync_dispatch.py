"""How sync automations are woken: per-trigger event filters, the settle window that
turns a burst of album edits into one run, the dispatcher tick that starts deferred
runs (but not while one is still going), and the routing to the sync run task."""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks import automation_dispatch, events
from yaffo.background_tasks.event_filters import trigger_matches
from yaffo.background_tasks.events import EventContext
from yaffo.background_tasks.tasks import dispatch_event as de
from yaffo.background_tasks.tasks import dispatcher as sched
from yaffo.background_tasks.tasks import sync_run
from yaffo.db import db
from yaffo.db.models import (
    Automation, AutomationTrigger, Job,
    ALBUM_CHANGE_ADDED, ALBUM_CHANGE_DELETED, EVENT_ALBUM_CHANGED, EVENT_MEDIA_DELETED, JOB_STATUS_RUNNING,
    TRIGGER_TYPE_EVENT,
)
from yaffo.utils.time import utcnow

pytestmark = pytest.mark.unit


class _Factory:
    def __init__(self, engine):
        self.engine = engine

    def __call__(self):
        return Session(self.engine)

    def remove(self):
        pass


@pytest.fixture
def engine(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(eng)
    monkeypatch.setattr(de, "SessionFactory", _Factory(eng))
    monkeypatch.setattr(sched, "SessionFactory", _Factory(eng))
    yield eng
    eng.dispose()


@pytest.fixture
def started(monkeypatch) -> list[int]:
    runs: list[int] = []
    monkeypatch.setattr(sync_run, "enqueue_sync_run", runs.append)
    return runs


def _sync(engine, *, album_id=7, settle=60) -> int:
    with Session(engine) as s:
        automation = Automation(slug="for-mom", name="For Mom", enabled=True, status="ACCEPTED",
                                sync={"config": {}, "runtime": {}})
        s.add(automation)
        s.flush()
        s.add(AutomationTrigger(automation_id=automation.id, trigger_type=TRIGGER_TYPE_EVENT,
                                event_type=EVENT_ALBUM_CHANGED, enabled=True,
                                config={"album_id": album_id, "sync_automation_id": automation.id,
                                        "settle_seconds": settle}))
        s.add(AutomationTrigger(automation_id=automation.id, trigger_type=TRIGGER_TYPE_EVENT,
                                event_type=EVENT_MEDIA_DELETED, enabled=True,
                                config={"sync_automation_id": automation.id, "settle_seconds": settle}))
        s.commit()
        return automation.id


def _next_run_at(engine, automation_id, event_type):
    with Session(engine) as s:
        return s.query(AutomationTrigger.next_run_at).filter_by(
            automation_id=automation_id, event_type=event_type).scalar()


def _trigger(event_type: str, config: dict) -> AutomationTrigger:
    return AutomationTrigger(trigger_type=TRIGGER_TYPE_EVENT, event_type=event_type, config=config)


def test_album_trigger_matches_only_its_album_and_changes():
    trigger = _trigger(EVENT_ALBUM_CHANGED, {"album_id": 7, "changes": ["added"]})
    assert trigger_matches(trigger, EventContext(EVENT_ALBUM_CHANGED, album_id=7, change="added"))
    assert not trigger_matches(trigger, EventContext(EVENT_ALBUM_CHANGED, album_id=8, change="added"))
    assert not trigger_matches(trigger, EventContext(EVENT_ALBUM_CHANGED, album_id=7, change="removed"))
    assert trigger_matches(_trigger(EVENT_ALBUM_CHANGED, None), EventContext(EVENT_ALBUM_CHANGED, album_id=1))


def test_media_deleted_trigger_matches_only_deletes_naming_its_sync():
    trigger = _trigger(EVENT_MEDIA_DELETED, {"sync_automation_id": 3})
    assert trigger_matches(trigger, EventContext(EVENT_MEDIA_DELETED, sync_automation_ids=[2, 3]))
    assert not trigger_matches(trigger, EventContext(EVENT_MEDIA_DELETED, sync_automation_ids=[2]))
    # An ordinary subscriber (no filter) hears every delete.
    assert trigger_matches(_trigger(EVENT_MEDIA_DELETED, {}), EventContext(EVENT_MEDIA_DELETED))


def test_a_burst_of_album_edits_defers_one_run(engine, started):
    automation_id = _sync(engine)

    for _ in range(5):
        de.dispatch_event_task.fn(EVENT_ALBUM_CHANGED, {"album_id": 7, "change": ALBUM_CHANGE_ADDED})
    de.dispatch_event_task.fn(EVENT_ALBUM_CHANGED, {"album_id": 8, "change": ALBUM_CHANGE_ADDED})

    assert started == []
    due = _next_run_at(engine, automation_id, EVENT_ALBUM_CHANGED)
    assert timedelta(seconds=50) < due - utcnow() <= timedelta(seconds=60)


def test_a_deleted_album_runs_at_once_so_the_sync_pauses(engine, started):
    automation_id = _sync(engine)

    de.dispatch_event_task.fn(EVENT_ALBUM_CHANGED, {"album_id": 7, "change": ALBUM_CHANGE_DELETED})

    assert started == [automation_id]


def test_deletes_wake_only_the_syncs_that_queued_removals(engine, started):
    automation_id = _sync(engine)

    de.dispatch_event_task.fn(EVENT_MEDIA_DELETED, {"media_item_ids": [1], "sync_automation_ids": [999]})
    assert _next_run_at(engine, automation_id, EVENT_MEDIA_DELETED) is None

    de.dispatch_event_task.fn(EVENT_MEDIA_DELETED, {"media_item_ids": [1], "sync_automation_ids": [automation_id]})
    assert _next_run_at(engine, automation_id, EVENT_MEDIA_DELETED) is not None


def test_the_tick_starts_a_due_deferred_run_once(engine, started):
    automation_id = _sync(engine)
    with Session(engine) as s:
        s.query(AutomationTrigger).filter_by(event_type=EVENT_ALBUM_CHANGED).update(
            {"next_run_at": utcnow() - timedelta(seconds=1)})
        s.commit()

    sched.dispatch_scheduled_tasks.fn()
    sched.dispatch_scheduled_tasks.fn()

    assert started == [automation_id]
    assert _next_run_at(engine, automation_id, EVENT_ALBUM_CHANGED) is None


def test_the_tick_waits_while_the_previous_run_is_still_going(engine, started):
    automation_id = _sync(engine)
    with Session(engine) as s:
        s.query(AutomationTrigger).filter_by(event_type=EVENT_ALBUM_CHANGED).update(
            {"next_run_at": utcnow() - timedelta(seconds=1)})
        s.add(Job(id="run-1", name="for-mom", automation_id=automation_id, status=JOB_STATUS_RUNNING))
        s.commit()

    sched.dispatch_scheduled_tasks.fn()

    assert started == []
    assert _next_run_at(engine, automation_id, EVENT_ALBUM_CHANGED) is not None


def test_sync_automations_route_to_the_sync_run_task(started):
    automation = Automation(id=4, slug="s", name="S", sync={"config": {}, "runtime": {}})
    assert automation_dispatch.invoke_automation(automation, None) is True
    assert started == [4]


def test_album_changed_payload_lists_ids_or_says_truncated(monkeypatch):
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(events, "emit_event", lambda event_type, payload: captured.append((event_type, payload)))

    events.emit_album_changed(7, ALBUM_CHANGE_ADDED, [1, 2])
    events.emit_album_changed(7, ALBUM_CHANGE_ADDED)

    assert captured == [
        (EVENT_ALBUM_CHANGED, {"album_id": 7, "change": "added", "media_item_ids": [1, 2], "truncated": False}),
        (EVENT_ALBUM_CHANGED, {"album_id": 7, "change": "added", "media_item_ids": [], "truncated": True}),
    ]
