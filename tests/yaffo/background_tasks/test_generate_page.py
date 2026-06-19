"""Unit tests for the generate_page background task (run_generation).

Drives the task's core against a throwaway SQLite database with the agent stubbed
(a _FakeAgent replaying canned AgentEvents), so widget/conversation persistence and
the IN_PROGRESS -> READY/FAILED transitions are exercised without a worker, an app
context, or the model.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.tasks import generate_page as task
from yaffo.db import db
from yaffo.db.models import (
    PAGE_VERSION_STATUS_FAILED,
    PAGE_VERSION_STATUS_IN_PROGRESS,
    PAGE_VERSION_STATUS_READY,
)
from yaffo.db.repositories import custom_page_repository as repo
from yaffo.site_agents.agent import AgentEvent

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    """A configured key by default; the agent is stubbed so it's never used to call
    a model, but run_generation now resolves it up front (and fails closed without)."""
    monkeypatch.setattr("yaffo.site_agents.llm_config.get_api_key", lambda: "test-key")


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


@pytest.fixture
def version(session):
    """A fresh page forked to an empty IN_PROGRESS working version."""
    page = repo.create_page(session, "Trip", subtitle="maine")
    return repo.fork_version(session, page.id)


class _FakeAgent:
    def __init__(self, events):
        self._events = events

    def run_events(self, user_message, should_cancel=None):
        yield from self._events


def _use_agent(monkeypatch, events):
    monkeypatch.setattr(task, "create_page_builder_agent", lambda *a, **k: _FakeAgent(events))


def _widget(wid="g1", title="Gallery", **overrides):
    w = {"id": wid, "title": title, "data_query": {}, "html": "<div>", "css": "", "js": "",
         "grid_x": None, "grid_y": None, "grid_w": 4, "grid_h": 3, "state": {}}
    w.update(overrides)
    return w


def _no_cancel():
    return False


class TestHappyPath:
    def test_marks_ready_on_clean_finish(self, session, version, monkeypatch):
        _use_agent(monkeypatch, [
            AgentEvent("assistant", text="Here is a gallery."),
            AgentEvent("tool", name="create_widget", tool_result_data=_widget()),
            AgentEvent("done"),
        ])
        task.run_generation(session, version.id, "make a gallery", should_cancel=_no_cancel)

        fetched = repo.get_version(session, version.id)
        assert fetched.status == PAGE_VERSION_STATUS_READY
        assert fetched.completed_at is not None
        # The task does not persist widgets itself — the widget tool writes them into
        # the version (covered in test_widget_tool); with the agent stubbed, none land.
        assert fetched.widgets == []

    def test_records_assistant_and_status_lines(self, session, version, monkeypatch):
        _use_agent(monkeypatch, [
            AgentEvent("assistant", text="Working on it."),
            AgentEvent("tool", name="create_widget", tool_result_data=_widget()),
            AgentEvent("done"),
        ])
        task.run_generation(session, version.id, "go", should_cancel=_no_cancel)

        types = [(m.type, m.content) for m in repo.get_version(session, version.id).messages]
        assert ("assistant", "Working on it.") in types
        assert ("status", "Creating widget…") in types

    def test_data_query_tool_adds_status_but_no_widget(self, session, version, monkeypatch):
        _use_agent(monkeypatch, [
            AgentEvent("tool", name="run_data_query", tool_result_data=None),
            AgentEvent("done"),
        ])
        task.run_generation(session, version.id, "go", should_cancel=_no_cancel)

        fetched = repo.get_version(session, version.id)
        assert fetched.widgets == []
        assert ("status", "Looking up information…") in [(m.type, m.content) for m in fetched.messages]


class TestFailure:
    def test_error_event_marks_failed_and_records_it(self, session, version, monkeypatch):
        _use_agent(monkeypatch, [
            AgentEvent("error", text="hit the output token limit", stop_reason="max_tokens"),
        ])
        task.run_generation(session, version.id, "go", should_cancel=_no_cancel)

        fetched = repo.get_version(session, version.id)
        assert fetched.status == PAGE_VERSION_STATUS_FAILED
        assert fetched.error == "hit the output token limit"
        assert ("error", "hit the output token limit") in [(m.type, m.content) for m in fetched.messages]

    def test_missing_api_key_fails_closed(self, session, version, monkeypatch):
        monkeypatch.setattr("yaffo.site_agents.llm_config.get_api_key", lambda: None)
        # the agent must never be built without a key.
        monkeypatch.setattr(task, "create_page_builder_agent",
                            lambda *a, **k: pytest.fail("agent built without a key"))
        task.run_generation(session, version.id, "go", should_cancel=_no_cancel)

        fetched = repo.get_version(session, version.id)
        assert fetched.status == PAGE_VERSION_STATUS_FAILED
        assert any(m.type == "error" and "API key" in m.content for m in fetched.messages)

    def test_unexpected_exception_marks_failed(self, session, version, monkeypatch):
        class _Boom:
            def run_events(self, user_message, should_cancel=None):
                raise RuntimeError("kaboom")
                yield  # pragma: no cover -- makes this a generator
        monkeypatch.setattr(task, "create_page_builder_agent", lambda *a, **k: _Boom())
        task.run_generation(session, version.id, "go", should_cancel=_no_cancel)

        fetched = repo.get_version(session, version.id)
        assert fetched.status == PAGE_VERSION_STATUS_FAILED
        assert "kaboom" in fetched.error
        assert any(m.type == "error" and "kaboom" in m.content for m in fetched.messages)


class TestCancellation:
    def test_cancelled_event_leaves_version_in_progress(self, session, version, monkeypatch):
        # The agent reports a cancel; run_generation must not flip to READY/FAILED --
        # the cancel route owns deleting the version and reverting.
        _use_agent(monkeypatch, [AgentEvent("cancelled", text="Generation cancelled.")])
        task.run_generation(session, version.id, "go", should_cancel=lambda: True)

        fetched = repo.get_version(session, version.id)
        assert fetched.status == PAGE_VERSION_STATUS_IN_PROGRESS
        assert fetched.completed_at is None


class TestMissingVersion:
    def test_missing_version_is_a_noop(self, session, monkeypatch):
        _use_agent(monkeypatch, [AgentEvent("done")])
        task.run_generation(session, 9999, "go", should_cancel=_no_cancel)  # does not raise