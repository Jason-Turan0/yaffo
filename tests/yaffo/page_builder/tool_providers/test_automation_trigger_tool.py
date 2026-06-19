"""Unit tests for the add_automation_trigger tool
(yaffo/site_agents/tool_providers/automation_trigger_tool.py).

The tool lets the automation-builder agent decide when its automation runs. These
exercise it against a throwaway database: adding schedule + event triggers, the
cron / event-type validation guards, idempotency (no duplicate rows), and the
not-found guard.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import (
    Automation,
    AutomationTrigger,
    EVENT_PHOTO_INDEXED,
    AUTOMATION_STATUS_READY,
    TRIGGER_TYPE_EVENT,
    TRIGGER_TYPE_SCHEDULE,
)
from yaffo.site_agents.tool_providers.automation_trigger_tool import (
    AutomationTriggerToolProvider,
)

pytestmark = pytest.mark.unit

SLUG = "my-auto"


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


@pytest.fixture
def automation(session):
    a = Automation(slug=SLUG, name="My auto", is_system=False, status=AUTOMATION_STATUS_READY)
    session.add(a)
    session.commit()
    return a


def _tool(session):
    return AutomationTriggerToolProvider(SLUG, session=session)


def _triggers(session):
    return session.query(AutomationTrigger).all()


def test_adds_schedule_trigger(session, automation):
    tool = _tool(session)

    result = tool.call_tool(tool.ADD, {"trigger_type": "schedule", "cron": "0 3 * * *"})

    assert "Added a schedule trigger" in result
    triggers = _triggers(session)
    assert len(triggers) == 1
    assert triggers[0].trigger_type == TRIGGER_TYPE_SCHEDULE
    assert triggers[0].cron == "0 3 * * *"
    assert triggers[0].enabled is True
    assert triggers[0].next_run_at is None


def test_adds_event_trigger(session, automation):
    tool = _tool(session)

    result = tool.call_tool(tool.ADD, {"trigger_type": "event", "event_type": EVENT_PHOTO_INDEXED})

    assert "Added an event trigger" in result
    triggers = _triggers(session)
    assert len(triggers) == 1
    assert triggers[0].trigger_type == TRIGGER_TYPE_EVENT
    assert triggers[0].event_type == EVENT_PHOTO_INDEXED


def test_rejects_invalid_cron(session, automation):
    tool = _tool(session)

    result = tool.call_tool(tool.ADD, {"trigger_type": "schedule", "cron": "not a cron"})

    assert "not a valid" in result
    assert _triggers(session) == []


def test_schedule_requires_cron(session, automation):
    tool = _tool(session)

    result = tool.call_tool(tool.ADD, {"trigger_type": "schedule"})

    assert "needs a `cron`" in result
    assert _triggers(session) == []


def test_rejects_unknown_event_type(session, automation):
    tool = _tool(session)

    result = tool.call_tool(tool.ADD, {"trigger_type": "event", "event_type": "made_up"})

    assert "must be one of" in result
    assert _triggers(session) == []


def test_rejects_unknown_trigger_type(session, automation):
    tool = _tool(session)

    result = tool.call_tool(tool.ADD, {"trigger_type": "weekly"})

    assert "trigger_type must be" in result
    assert _triggers(session) == []


def test_duplicate_schedule_is_idempotent(session, automation):
    tool = _tool(session)
    tool.call_tool(tool.ADD, {"trigger_type": "schedule", "cron": "0 3 * * *"})

    result = tool.call_tool(tool.ADD, {"trigger_type": "schedule", "cron": "0 3 * * *"})

    assert "Already has a schedule trigger" in result
    assert len(_triggers(session)) == 1


def test_duplicate_event_is_idempotent(session, automation):
    tool = _tool(session)
    tool.call_tool(tool.ADD, {"trigger_type": "event", "event_type": EVENT_PHOTO_INDEXED})

    result = tool.call_tool(tool.ADD, {"trigger_type": "event", "event_type": EVENT_PHOTO_INDEXED})

    assert "Already triggers on" in result
    assert len(_triggers(session)) == 1


def test_missing_automation(session):
    tool = AutomationTriggerToolProvider("ghost", session=session)

    result = tool.call_tool(tool.ADD, {"trigger_type": "schedule", "cron": "0 3 * * *"})

    assert "not found" in result
    assert _triggers(session) == []


def test_removes_schedule_trigger(session, automation):
    tool = _tool(session)
    tool.call_tool(tool.ADD, {"trigger_type": "schedule", "cron": "0 3 * * *"})

    result = tool.call_tool(tool.REMOVE, {"trigger_type": "schedule", "cron": "0 3 * * *"})

    assert "Removed the schedule trigger" in result
    assert _triggers(session) == []


def test_removes_event_trigger(session, automation):
    tool = _tool(session)
    tool.call_tool(tool.ADD, {"trigger_type": "event", "event_type": EVENT_PHOTO_INDEXED})

    result = tool.call_tool(tool.REMOVE, {"trigger_type": "event", "event_type": EVENT_PHOTO_INDEXED})

    assert "Removed the event trigger" in result
    assert _triggers(session) == []


def test_remove_only_targets_the_match(session, automation):
    tool = _tool(session)
    tool.call_tool(tool.ADD, {"trigger_type": "schedule", "cron": "0 3 * * *"})
    tool.call_tool(tool.ADD, {"trigger_type": "event", "event_type": EVENT_PHOTO_INDEXED})

    tool.call_tool(tool.REMOVE, {"trigger_type": "schedule", "cron": "0 3 * * *"})

    triggers = _triggers(session)
    assert len(triggers) == 1
    assert triggers[0].trigger_type == TRIGGER_TYPE_EVENT


def test_remove_nonexistent_schedule_is_reported(session, automation):
    tool = _tool(session)

    result = tool.call_tool(tool.REMOVE, {"trigger_type": "schedule", "cron": "0 3 * * *"})

    assert "No schedule trigger" in result


def test_remove_nonexistent_event_is_reported(session, automation):
    tool = _tool(session)

    result = tool.call_tool(tool.REMOVE, {"trigger_type": "event", "event_type": EVENT_PHOTO_INDEXED})

    assert "No 'photo_indexed' event trigger" in result
