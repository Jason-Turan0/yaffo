"""Unit tests for the custom-automation executor (automation_sandbox/executor).

Runs real Starlark through run_automation with a fake host (resolve_query
monkeypatched), asserting: the script reads its trigger `ctx`, reaches the host
API bound to the run's session, and that bad/empty code comes back as a failed
StarlarkResult rather than raising.
"""
import pytest

from yaffo.background_tasks.automation_sandbox.executor import run_automation
from yaffo.background_tasks.events import EventContext

pytestmark = pytest.mark.unit


class _FakeAutomation:
    def __init__(self, code, slug="custom"):
        self.published_code = code
        self.slug = slug


def test_script_reads_ctx_and_calls_host_api(monkeypatch):
    seen = []

    def fake_resolve_query(session, query):
        seen.append((session, query))
        return [{"id": 7}]

    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.resolve_query",
        fake_resolve_query,
    )
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.enrich_photo_rows",
        lambda session, rows: rows,  # enrichment tested separately
    )

    session = object()
    code = "data_query({'source': 'media_items', 'in_ids': ctx['media_ids']})"
    context = EventContext(event_type="photo_indexed", job_id="job-1", media_ids=[1, 2])

    result = run_automation(session, _FakeAutomation(code), context)

    assert result.success is True, result.error
    assert result.value == [{"id": 7}]
    # the host call ran with the run's session and saw ctx injected into the script
    assert len(seen) == 1
    called_session, called_query = seen[0]
    assert called_session is session
    assert called_query == {"source": "media_items", "in_ids": [1, 2]}


def test_schedule_run_has_empty_ctx(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.resolve_query",
        lambda session, query: query,
    )
    # context=None (a schedule trigger) -> ctx fields are empty/None
    code = "data_query({'event': ctx['event_type'], 'ids': ctx['media_ids']})"
    result = run_automation(object(), _FakeAutomation(code), None)
    assert result.success is True, result.error
    assert result.value == {"event": None, "ids": []}


def test_empty_code_returns_failure_not_exception():
    result = run_automation(object(), _FakeAutomation(""), None)
    assert result.success is False
    assert result.error


def test_bad_script_is_failure_data():
    result = run_automation(object(), _FakeAutomation("this is not valid starlark ("), None)
    assert result.success is False
    assert result.error
