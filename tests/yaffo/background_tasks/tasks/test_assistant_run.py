"""assistant_run: answers a conversation's latest message and records the run.

The agent is replaced by a scripted stand-in, so nothing reaches a model."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.tasks import assistant_run as run_module
from yaffo.db import db
from yaffo.db.models import (
    ASSISTANT_STATUS_FAILED, ASSISTANT_STATUS_IDLE, ASSISTANT_STATUS_RUNNING, JOB_STATUS_FAILED, ApplicationSettings,
    Automation, Job,
)
from yaffo.db.repositories import assistant_repository as repo
from yaffo.site_agents.agent import AgentEvent
from yaffo.site_agents.assistant.tool_providers.diagnostics.diagnostics import DiagnosticsToolProvider
from yaffo.taskq.store import Store

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("yaffo.site_agents.assistant.call_logs.LOG_ROOT", tmp_path / "assistant_logs")
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess


class ScriptedAgent:
    def __init__(self, events):
        self.events = events
        self.messages = []

    def run_events(self, user_message, should_cancel=None):
        self.messages.append(user_message)
        yield from self.events


@pytest.fixture
def agent_factory(monkeypatch):
    """Install a scripted agent; records the history and model it was built with."""
    calls = {}

    def install(events, api_key="key", model="claude-haiku-4-5-20251001"):
        agent = ScriptedAgent(events)

        def create(*, model, api_key, history, session=None, diagnostics=frozenset(), redactor=None, model_label="",
                   log_dir=None, conversation_id=None, actions=frozenset()):
            calls.update(model=model, api_key=api_key, history=history, diagnostics=diagnostics,
                         redactor=redactor, model_label=model_label, conversation_id=conversation_id,
                         actions=actions)
            return agent

        monkeypatch.setattr(run_module, "create_assistant_agent", create)
        monkeypatch.setattr(run_module.assistant_settings, "api_key", lambda session=None: api_key)
        monkeypatch.setattr(run_module.assistant_settings, "resolve_model", lambda session=None: model)
        monkeypatch.setattr(run_module.assistant_settings, "provider_label", lambda session=None: "Anthropic")
        return agent

    install.calls = calls
    return install


def _conversation(session, *turns):
    conversation = repo.create_conversation(session, "Q")
    for kind, text in turns:
        repo.add_event(session, conversation.id, kind, text)
    repo.start_run(session, conversation.id, "m")
    return conversation.id


def _events(session, conversation_id):
    return [(e.kind, e.content, json.loads(e.payload) if e.payload else None)
            for e in repo.list_events(session, conversation_id)]


def test_records_tools_and_answer_then_goes_idle(session, agent_factory):
    tool_payload = {"tool": "search_docs", "query": "folders", "count": 2, "sources": []}
    agent = agent_factory([
        AgentEvent("tool", name="search_docs", tool_result_data=tool_payload),
        AgentEvent("assistant", text="  Open Settings → Media Directories.  "),
        AgentEvent("done", stop_reason="end_turn"),
    ])
    conversation_id = _conversation(session, ("user", "How do I add folders?"))

    run_module.run_assistant_turn(session, conversation_id, should_cancel=lambda: False)

    assert _events(session, conversation_id)[1:] == [
        ("tool", "", tool_payload),
        ("assistant", "Open Settings → Media Directories.", None),
    ]
    assert repo.get_conversation(session, conversation_id).status == ASSISTANT_STATUS_IDLE
    assert "How do I add folders?" in agent.messages[0]
    assert "<application_locale>en</application_locale>" in agent.messages[0]


def test_earlier_turns_are_replayed_as_history(session, agent_factory):
    agent_factory([AgentEvent("done")])
    conversation_id = _conversation(
        session, ("user", "first"), ("assistant", "answer"), ("user", "follow-up"))

    run_module.run_assistant_turn(session, conversation_id, should_cancel=lambda: False)

    assert agent_factory.calls["history"] == [("user", "first"), ("assistant", "answer")]


def test_missing_api_key_fails_with_a_code(session, agent_factory):
    agent_factory([], api_key=None)
    conversation_id = _conversation(session, ("user", "hi"))

    run_module.run_assistant_turn(session, conversation_id, should_cancel=lambda: False)

    kind, _text, payload = _events(session, conversation_id)[-1]
    assert kind == "error"
    assert payload == {"code": "api_key_missing", "provider": "Anthropic"}
    assert repo.get_conversation(session, conversation_id).status == ASSISTANT_STATUS_FAILED


@pytest.mark.parametrize("stop_reason, code", [
    ("max_tokens", "output_limit"),
    ("max_iterations", "iteration_limit"),
    ("error", "model_error"),
])
def test_agent_errors_are_recorded_with_codes(session, agent_factory, stop_reason, code):
    agent_factory([AgentEvent("error", text="boom", stop_reason=stop_reason)])
    conversation_id = _conversation(session, ("user", "hi"))

    run_module.run_assistant_turn(session, conversation_id, should_cancel=lambda: False)

    assert _events(session, conversation_id)[-1] == ("error", "boom", {"code": code})
    assert repo.get_conversation(session, conversation_id).status == ASSISTANT_STATUS_FAILED


def test_an_exception_fails_the_run_instead_of_the_worker(session, agent_factory):
    class Exploding:
        def run_events(self, *_args, **_kwargs):
            raise RuntimeError("provider down")
            yield  # pragma: no cover

    agent_factory([])
    run_module.create_assistant_agent = lambda **_kwargs: Exploding()
    conversation_id = _conversation(session, ("user", "hi"))

    run_module.run_assistant_turn(session, conversation_id, should_cancel=lambda: False)

    kind, text, payload = _events(session, conversation_id)[-1]
    assert (kind, payload) == ("error", {"code": "run_failed"})
    assert "provider down" in text


def test_cancel_stops_without_recording_further_output(session, agent_factory):
    agent_factory([
        AgentEvent("assistant", text="partial"),
        AgentEvent("assistant", text="more"),
    ])
    conversation_id = _conversation(session, ("user", "hi"))
    repo.set_status(session, conversation_id, ASSISTANT_STATUS_IDLE)  # the cancel route

    run_module.run_assistant_turn(
        session, conversation_id,
        should_cancel=lambda: repo.get_conversation(session, conversation_id).status != ASSISTANT_STATUS_RUNNING,
    )

    assert [kind for kind, _t, _p in _events(session, conversation_id)] == ["user"]


def test_nothing_to_answer_settles_idle(session, agent_factory):
    agent_factory([AgentEvent("assistant", text="should not run")])
    conversation_id = _conversation(session, ("user", "q"), ("assistant", "a"))

    run_module.run_assistant_turn(session, conversation_id, should_cancel=lambda: False)

    assert repo.get_conversation(session, conversation_id).status == ASSISTANT_STATUS_IDLE
    assert len(_events(session, conversation_id)) == 2


def test_passes_enabled_diagnostics_and_attached_context(session, agent_factory):
    agent = agent_factory([AgentEvent("done", stop_reason="end_turn")])
    conversation = repo.create_conversation(session, "Q")
    repo.add_event(session, conversation.id, "user", "Why did this fail?",
                   {"context": {"page": "Utilities → Index Photos", "error_code": "filesystem_scan_failed"}})
    repo.start_run(session, conversation.id, "m")

    run_module.run_assistant_turn(session, conversation.id, should_cancel=lambda: False)

    assert agent_factory.calls["diagnostics"] == frozenset({"logs", "library", "files", "jobs"})
    assert "Anthropic" in agent_factory.calls["model_label"]
    assert "<error_code>filesystem_scan_failed</error_code>" in agent.messages[0]
    assert "<page>Utilities → Index Photos</page>" in agent.messages[0]



def test_follow_up_includes_prior_tool_evidence(session, agent_factory):
    agent_factory([AgentEvent("done")])
    conversation_id = _conversation(session, ("user", "check"))
    repo.add_event(session, conversation_id, "tool", "", {"tool": "read_log", "detail": "disk failed"})
    repo.add_event(session, conversation_id, "assistant", "There is a disk error.")
    repo.add_event(session, conversation_id, "user", "Which disk?")
    run_module.run_assistant_turn(session, conversation_id, should_cancel=lambda: False)
    assert "disk failed" in agent_factory.calls["history"][1][1]
    assert "historical_tool_result" in agent_factory.calls["history"][1][1]


# ---- contextual checks run before the model starts (context_prefetch.py) ----------------

@pytest.fixture
def queue_store(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "queue.db"))
    monkeypatch.setattr(DiagnosticsToolProvider, "store", property(lambda self: store))
    return store


def _ask_from(session, context):
    conversation = repo.create_conversation(session, "Q")
    repo.add_event(session, conversation.id, "user", "Why did this fail?", {"context": context})
    repo.start_run(session, conversation.id, "m")
    return conversation.id


def test_a_failed_job_is_looked_up_before_the_model_starts(session, agent_factory, queue_store):
    agent = agent_factory([AgentEvent("assistant", text="It failed."), AgentEvent("done")])
    session.add_all([Automation(id=1, slug="file-sync", name="File Sync", enabled=True),
                     Job(id="j1", name="file_sync", status=JOB_STATUS_FAILED, automation_id=1, message="Drive gone")])
    session.commit()
    conversation_id = _ask_from(session, {"job_id": "j1", "automation": "file-sync"})

    run_module.run_assistant_turn(session, conversation_id, should_cancel=lambda: False)

    events = _events(session, conversation_id)
    assert [kind for kind, _, _ in events] == ["user", "tool", "tool", "assistant"]
    assert [payload["tool"] for kind, _, payload in events if kind == "tool"] == ["job_detail", "automation_runs"]
    message = agent.messages[0]
    assert "<prefetched>" in message and "Message: Drive gone" in message
    assert '<data source="automation_runs">' in message


def test_no_lookup_when_jobs_are_switched_off_or_the_job_is_gone(session, agent_factory, queue_store):
    agent = agent_factory([AgentEvent("done")])
    conversation_id = _ask_from(session, {"job_id": "missing"})
    run_module.run_assistant_turn(session, conversation_id, should_cancel=lambda: False)
    assert "<prefetched>" not in agent.messages[0]
    assert [kind for kind, _, _ in _events(session, conversation_id)] == ["user"]

    session.add_all([Job(id="j2", name="file_sync", status=JOB_STATUS_FAILED),
                     ApplicationSettings(name="assistant_diag_jobs", type="string", value="false")])
    session.commit()
    agent = agent_factory([AgentEvent("done")])
    conversation_id = _ask_from(session, {"job_id": "j2"})
    run_module.run_assistant_turn(session, conversation_id, should_cancel=lambda: False)
    assert "<prefetched>" not in agent.messages[0]
    assert [kind for kind, _, _ in _events(session, conversation_id)] == ["user"]
