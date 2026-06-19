"""Tests for the automation loop guard, Mechanism 1 (in-process causal chain).

Three layers:
- emit_event stamps the active causal chain (event_chain_scope) onto every event,
  with correct nesting and an empty chain outside any run.
- dispatch_event_task skips an automation already in the chain and logs it, while a
  fresh subscriber still runs.
- end-to-end (immediate mode): an automation that emits the very event it subscribes
  to fires exactly once — the re-dispatch is guarded — and the skip is logged.
"""
import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks import events
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.events import emit_event, event_chain_scope
from yaffo.background_tasks.registry import HANDLERS
from yaffo.background_tasks.tasks import dispatch_event as de
from yaffo.db import db
from yaffo.db.models import (
    Automation,
    AutomationTrigger,
    EVENT_PHOTO_LABELED,
    TRIGGER_TYPE_EVENT,
)

pytestmark = pytest.mark.unit


# ---- Mechanism 1a: emit_event stamps the causal chain ---------------------

def test_emit_stamps_chain_from_scope(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(
        "yaffo.background_tasks.tasks.dispatch_event.dispatch_event_task",
        lambda event_type, payload: captured.append(payload),
    )

    emit_event("e", {"photo_ids": [1]})  # no scope -> genuine origin
    assert captured[-1]["origin_automation_ids"] == []

    with event_chain_scope([], 5):
        emit_event("e", {})
        assert captured[-1]["origin_automation_ids"] == [5]
        with event_chain_scope([5], 7):  # a nested run accumulates
            emit_event("e", {})
            assert captured[-1]["origin_automation_ids"] == [5, 7]
        emit_event("e", {})  # inner scope reset
        assert captured[-1]["origin_automation_ids"] == [5]

    emit_event("e", {})  # back outside any scope
    assert captured[-1]["origin_automation_ids"] == []


# ---- Mechanism 1b/1c: the dispatch guard ----------------------------------

@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(eng)
    yield eng
    eng.dispose()


class _FakeSessionFactory:
    """A non-scoped session factory: each call returns a fresh Session so the
    recursive immediate-mode dispatch can't close/remove an outer session (the real
    scoped_session is per-thread; production never recurses in one thread)."""

    def __init__(self, engine):
        self.engine = engine

    def __call__(self):
        return Session(self.engine)

    def remove(self):
        pass


def _seed_automation(engine, *, handler=None, enabled=True):
    with Session(engine) as s:
        a = Automation(
            slug="loopy", name="Loopy", is_system=handler is not None,
            enabled=enabled, handler=handler, status="READY",
        )
        s.add(a)
        s.flush()
        s.add(AutomationTrigger(
            automation_id=a.id, trigger_type=TRIGGER_TYPE_EVENT,
            enabled=True, event_type=EVENT_PHOTO_LABELED,
        ))
        s.commit()
        return a.id


def test_dispatch_skips_automation_already_in_chain(engine, monkeypatch, caplog):
    automation_id = _seed_automation(engine)
    monkeypatch.setattr(de, "SessionFactory", _FakeSessionFactory(engine))
    calls: list = []
    monkeypatch.setattr(de, "invoke_automation", lambda a, c: calls.append(a.id) or True)
    monkeypatch.setattr(de.logger, "propagate", True)  # let caplog see the warning

    with caplog.at_level(logging.WARNING):
        de.dispatch_event_task.fn(
            EVENT_PHOTO_LABELED, {"origin_automation_ids": [automation_id]}
        )

    assert calls == []  # the automation in the chain was not invoked
    assert any("loop guard" in r.message for r in caplog.records)


def test_dispatch_runs_automation_not_in_chain(engine, monkeypatch):
    automation_id = _seed_automation(engine)
    monkeypatch.setattr(de, "SessionFactory", _FakeSessionFactory(engine))
    calls: list = []
    monkeypatch.setattr(de, "invoke_automation", lambda a, c: calls.append(a.id) or True)

    de.dispatch_event_task.fn(EVENT_PHOTO_LABELED, {"origin_automation_ids": [999]})

    assert calls == [automation_id]  # a fresh subscriber still runs


def test_self_emitting_automation_fires_once(engine, monkeypatch, caplog):
    """End-to-end: a system automation whose handler emits the event it subscribes to
    runs exactly once — the re-dispatch is guarded — driven synchronously in immediate
    mode."""
    automation_id = _seed_automation(engine, handler="loop_test")
    monkeypatch.setattr(de, "SessionFactory", _FakeSessionFactory(engine))
    monkeypatch.setattr(task_queue, "immediate", True)
    monkeypatch.setattr(de.logger, "propagate", True)

    runs: list = []

    def _emitting_handler(automation, context):
        runs.append(automation.id)
        # Mirror the real emitters: scope the run so the re-emit carries this automation.
        with event_chain_scope(context.origin_automation_ids, automation.id):
            emit_event(EVENT_PHOTO_LABELED, {"photo_ids": [1]})

    monkeypatch.setitem(HANDLERS, "loop_test", _emitting_handler)

    with caplog.at_level(logging.WARNING):
        emit_event(EVENT_PHOTO_LABELED, {"photo_ids": [1]})  # genuine origin, empty chain

    assert runs == [automation_id]  # ran once; the self-triggered re-dispatch was skipped
    assert any("loop guard" in r.message for r in caplog.records)
