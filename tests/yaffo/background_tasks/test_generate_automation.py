"""The generate_automation background task (run_automation_generation), with the agent
stubbed, against a throwaway SQLite database."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.tasks import generate_automation as task
from yaffo.db import db
from yaffo.db.models import (
    AUTOMATION_STATUS_IN_PROGRESS,
    AUTOMATION_STATUS_READY,
    CONVERSATION_TYPE_ASSISTANT,
    CONVERSATION_TYPE_STATUS,
    CONVERSATION_TYPE_USER,
    Automation,
)
from yaffo.db.repositories import automation_repository as repo
from yaffo.site_agents.agent import AgentEvent

pytestmark = pytest.mark.unit

SLUG = "tag-beach-photos"


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setattr("yaffo.site_agents.llm_config.get_api_key", lambda *a, **k: "test-key")


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


@pytest.fixture
def automation(session):
    row = Automation(slug=SLUG, name="Tag beach photos", status=AUTOMATION_STATUS_IN_PROGRESS,
                     working_code="tag_photos([], 'beach')")
    session.add(row)
    session.commit()
    return row


class _FakeAgent:
    def __init__(self, events):
        self._events = events

    def run_events(self, user_message, should_cancel=None):
        yield from self._events


def test_a_follow_up_sends_the_earlier_turns(session, automation, monkeypatch):
    seen = {}

    def agent(*args, **kwargs):
        seen["history"] = kwargs.get("history")
        return _FakeAgent([AgentEvent("done")])
    monkeypatch.setattr(task, "create_automation_builder_agent", agent)
    repo.add_message(session, automation.id, CONVERSATION_TYPE_USER, "tag beach photos")
    repo.add_message(session, automation.id, CONVERSATION_TYPE_STATUS, "Writing the automation…")
    repo.add_message(session, automation.id, CONVERSATION_TYPE_ASSISTANT, "Done. Skipping videos.")
    repo.add_message(session, automation.id, CONVERSATION_TYPE_USER, "also lakes")

    task.run_automation_generation(session, SLUG, "also lakes", should_cancel=lambda: False)

    assert seen["history"] == [("user", "tag beach photos"), ("assistant", "Done. Skipping videos.")]
    assert repo.get_by_slug(session, SLUG).status == AUTOMATION_STATUS_READY
