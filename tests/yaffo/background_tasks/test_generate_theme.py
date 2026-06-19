"""Unit tests for the generate_theme background task (run_theme_generation).

Drives the task's core against a throwaway SQLite database with the agent stubbed
(a _FakeAgent replaying canned AgentEvents), so the conversation persistence and the
IN_PROGRESS -> READY/FAILED transitions are exercised without a worker, an app
context, or the model. Mirrors test_generate_page.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo import themes
from yaffo.background_tasks.tasks import generate_theme as task
from yaffo.db import db
from yaffo.db.models import (
    PAGE_VERSION_STATUS_FAILED,
    PAGE_VERSION_STATUS_IN_PROGRESS,
    PAGE_VERSION_STATUS_READY,
)
from yaffo.site_agents.agent import AgentEvent
from yaffo.themes import CustomTheme, ThemeAssets

pytestmark = pytest.mark.unit

SLUG = "vapor-wave"


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    """A configured key by default; the agent is stubbed so it's never used to call
    a model, but run_theme_generation resolves it up front (and fails closed without)."""
    monkeypatch.setattr("yaffo.site_agents.llm_config.get_api_key", lambda: "test-key")


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


@pytest.fixture
def theme(session):
    """A custom theme already flipped to IN_PROGRESS (as the chat route leaves it
    before enqueuing the task)."""
    themes.save_custom_theme(
        CustomTheme(
            slug=SLUG,
            label="Vapor Wave",
            status=PAGE_VERSION_STATUS_IN_PROGRESS,
            conversations=[],
            published_theme=ThemeAssets(tokens_css=f'[data-theme="{SLUG}"] {{\n}}\n'),
            working_theme=None,
        ),
        session,
    )
    return SLUG


class _FakeAgent:
    def __init__(self, events):
        self._events = events

    def run_events(self, user_message, should_cancel=None):
        yield from self._events


def _use_agent(monkeypatch, events):
    monkeypatch.setattr(task, "create_theme_builder_agent", lambda *a, **k: _FakeAgent(events))


def _no_cancel():
    return False


def _reload(session, slug=SLUG):
    return themes.get_custom_theme(slug, session)


class TestHappyPath:
    def test_marks_ready_on_clean_finish(self, session, theme, monkeypatch):
        _use_agent(monkeypatch, [
            AgentEvent("assistant", text="Here is a neon theme."),
            AgentEvent("tool", name="write_theme", tool_result_data={}),
            AgentEvent("done"),
        ])
        task.run_theme_generation(session, theme, "make it neon", should_cancel=_no_cancel)

        assert _reload(session).status == PAGE_VERSION_STATUS_READY

    def test_records_assistant_and_status_lines(self, session, theme, monkeypatch):
        _use_agent(monkeypatch, [
            AgentEvent("assistant", text="Working on it."),
            AgentEvent("tool", name="write_theme", tool_result_data={}),
            AgentEvent("done"),
        ])
        task.run_theme_generation(session, theme, "go", should_cancel=_no_cancel)

        feed = [(m.type, m.content) for m in _reload(session).conversations]
        assert ("assistant", "Working on it.") in feed
        assert ("status", "Designing the theme…") in feed

    def test_unknown_tool_uses_generic_status(self, session, theme, monkeypatch):
        _use_agent(monkeypatch, [
            AgentEvent("tool", name="mystery_tool", tool_result_data=None),
            AgentEvent("done"),
        ])
        task.run_theme_generation(session, theme, "go", should_cancel=_no_cancel)

        assert ("status", "Working…") in [(m.type, m.content) for m in _reload(session).conversations]


class TestFailure:
    def test_error_event_marks_failed_and_records_it(self, session, theme, monkeypatch):
        _use_agent(monkeypatch, [
            AgentEvent("error", text="hit the output token limit", stop_reason="max_tokens"),
        ])
        task.run_theme_generation(session, theme, "go", should_cancel=_no_cancel)

        reloaded = _reload(session)
        assert reloaded.status == PAGE_VERSION_STATUS_FAILED
        assert ("error", "hit the output token limit") in [(m.type, m.content) for m in reloaded.conversations]

    def test_missing_api_key_fails_closed(self, session, theme, monkeypatch):
        monkeypatch.setattr("yaffo.site_agents.llm_config.get_api_key", lambda: None)
        # the agent must never be built without a key.
        monkeypatch.setattr(task, "create_theme_builder_agent",
                            lambda *a, **k: pytest.fail("agent built without a key"))
        task.run_theme_generation(session, theme, "go", should_cancel=_no_cancel)

        reloaded = _reload(session)
        assert reloaded.status == PAGE_VERSION_STATUS_FAILED
        assert any(m.type == "error" and "API key" in m.content for m in reloaded.conversations)

    def test_unexpected_exception_marks_failed(self, session, theme, monkeypatch):
        class _Boom:
            def run_events(self, user_message, should_cancel=None):
                raise RuntimeError("kaboom")
                yield  # pragma: no cover -- makes this a generator
        monkeypatch.setattr(task, "create_theme_builder_agent", lambda *a, **k: _Boom())
        task.run_theme_generation(session, theme, "go", should_cancel=_no_cancel)

        reloaded = _reload(session)
        assert reloaded.status == PAGE_VERSION_STATUS_FAILED
        assert any(m.type == "error" and "kaboom" in m.content for m in reloaded.conversations)


class TestCancellation:
    def test_cancelled_event_leaves_in_progress(self, session, theme, monkeypatch):
        # The agent reports a cancel; run_theme_generation must not flip to READY/FAILED
        # -- the cancel route owns the terminal status.
        _use_agent(monkeypatch, [AgentEvent("cancelled", text="Generation cancelled.")])
        task.run_theme_generation(session, theme, "go", should_cancel=lambda: True)

        assert _reload(session).status == PAGE_VERSION_STATUS_IN_PROGRESS


class TestMissingTheme:
    def test_missing_theme_is_a_noop(self, session, monkeypatch):
        _use_agent(monkeypatch, [AgentEvent("done")])
        task.run_theme_generation(session, "nope", "go", should_cancel=_no_cancel)  # does not raise