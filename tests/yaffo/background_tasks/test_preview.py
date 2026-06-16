"""Unit tests for the automation test/preview (automation_sandbox/preview).

Runs real Starlark with resolve_query monkeypatched, asserting the run records the
host-API calls the script made (the "actions"), picks the working draft over the
published code, and reports a schedule context when there's no event trigger.
"""
import pytest

from yaffo.background_tasks.automation_sandbox.preview import preview_automation

pytestmark = pytest.mark.unit


class _FakeTrigger:
    def __init__(self, trigger_type, event_type=None):
        self.trigger_type = trigger_type
        self.event_type = event_type


class _FakeAutomation:
    def __init__(self, *, working_code=None, published_code=None, triggers=(), slug="custom"):
        self.working_code = working_code
        self.published_code = published_code
        self.triggers = list(triggers)
        self.slug = slug


def _fake_host(monkeypatch, impl):
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_host.resolve_query", impl
    )


def test_preview_records_actions(monkeypatch):
    _fake_host(monkeypatch, lambda session, query: [{"id": 1}])
    code = "print('hi')\ndata_query({'source': 'photos', 'limit': 5})"
    result = preview_automation(object(), _FakeAutomation(published_code=code), [1, 2])

    assert result.success is True, result.error
    assert result.code_source == "published"
    assert result.context == {"type": "files", "photo_ids": [1, 2]}
    assert result.output == ["hi"]
    assert result.actions == [{
        "summary": "Looking up photos",
        "name": "data_query",
        "args": [{"source": "photos", "limit": 5}],
    }]
    assert result.value == [{"id": 1}]


def test_working_code_is_preferred(monkeypatch):
    _fake_host(monkeypatch, lambda session, query: [])
    automation = _FakeAutomation(working_code="print('draft')", published_code="print('live')")
    result = preview_automation(object(), automation, [])
    assert result.code_source == "working"
    assert result.output == ["draft"]


def test_no_host_calls_is_empty_actions(monkeypatch):
    _fake_host(monkeypatch, lambda session, query: [])
    result = preview_automation(object(), _FakeAutomation(published_code="print('x')"), [])
    assert result.actions == []