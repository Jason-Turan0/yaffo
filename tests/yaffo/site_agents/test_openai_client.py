"""OpenAICompatibleModelClient: request building + streamed-response parsing, with the
openai SDK stubbed so nothing hits the network. Covers the two schema quirks the client
normalizes — JSON-string tool arguments parsed to a dict, and finish_reason 'length'
mapped to Anthropic's 'max_tokens' (the value the agent loop checks for)."""
from types import SimpleNamespace

import pytest

from yaffo.site_agents.model_clients.model_client_types import ToolCallResult
from yaffo.site_agents.model_clients.openai_client import OpenAICompatibleModelClient

pytestmark = pytest.mark.unit


class _FakeStream:
    """Iterable context manager mimicking the openai streaming response."""
    def __init__(self, chunks):
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._chunks)


def _choice(*, content=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(delta=delta, finish_reason=finish_reason)


def _tc(index, *, id=None, name=None, arguments=None):
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments))


def _client(monkeypatch, chunks):
    client = OpenAICompatibleModelClient(
        model="deepseek-chat", provider_id="deepseek", base_url="https://api.deepseek.com/v1",
        system_prompt="SYS", tools=[{"type": "function", "function": {"name": "t"}}], api_key="x",
    )
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeStream(chunks)

    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    return client, captured


def test_parses_text_tool_calls_and_usage(monkeypatch):
    chunks = [
        SimpleNamespace(usage=None, choices=[_choice(content="Hel")]),
        SimpleNamespace(usage=None, choices=[_choice(content="lo")]),
        SimpleNamespace(usage=None, choices=[_choice(tool_calls=[_tc(0, id="call_1", name="get_x", arguments='{"a":')])]),
        SimpleNamespace(usage=None, choices=[_choice(tool_calls=[_tc(0, arguments='1}')], finish_reason="tool_calls")]),
        SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20,
                                  prompt_tokens_details=SimpleNamespace(cached_tokens=40)),
            choices=[]),
    ]
    client, _ = _client(monkeypatch, chunks)

    resp = client.call_model_api()

    assert resp.text == "Hello"
    assert resp.stop_reason == "tool_use"  # tool_calls -> tool_use
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert call.id == "call_1" and call.name == "get_x"
    assert call.input == {"a": 1}  # JSON-string args concatenated then parsed
    assert resp.usage.input_tokens == 100
    assert resp.usage.output_tokens == 20
    assert resp.usage.cache_read_tokens == 40


def test_length_finish_reason_maps_to_max_tokens(monkeypatch):
    chunks = [SimpleNamespace(usage=None, choices=[_choice(content="x", finish_reason="length")])]
    client, _ = _client(monkeypatch, chunks)
    resp = client.call_model_api()
    assert resp.stop_reason == "max_tokens"


def test_build_params_includes_system_first_and_tools(monkeypatch):
    chunks = [SimpleNamespace(usage=None, choices=[_choice(content="hi", finish_reason="stop")])]
    client, captured = _client(monkeypatch, chunks)
    client.add_user_message("hello there")
    client.call_model_api()

    assert captured["messages"][0] == {"role": "system", "content": "SYS"}
    assert captured["messages"][1] == {"role": "user", "content": "hello there"}
    assert captured["tools"]
    assert captured["stream"] is True
    # deepseek takes legacy max_tokens; openai would take max_completion_tokens.
    assert "max_tokens" in captured and "max_completion_tokens" not in captured


def test_openai_provider_uses_max_completion_tokens(monkeypatch):
    chunks = [SimpleNamespace(usage=None, choices=[_choice(content="hi", finish_reason="stop")])]
    client = OpenAICompatibleModelClient(
        model="gpt-5.1", provider_id="openai", base_url="https://api.openai.com/v1",
        system_prompt="S", api_key="x")
    captured = {}
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: captured.update(kw) or _FakeStream(chunks))))
    client.call_model_api()
    assert "max_completion_tokens" in captured and "max_tokens" not in captured


def test_tool_result_messages_are_role_tool(monkeypatch):
    chunks = [SimpleNamespace(usage=None, choices=[_choice(content="ok", finish_reason="stop")])]
    client, captured = _client(monkeypatch, chunks)
    client.add_user_message("go")
    client.add_tool_result_message([ToolCallResult("call_1", "get_x", "the result")])
    client.call_model_api()

    tool_msgs = [m for m in captured["messages"] if m.get("role") == "tool"]
    assert tool_msgs == [{"role": "tool", "tool_call_id": "call_1", "content": "the result"}]


def test_assistant_turn_with_tool_calls_is_replayed(monkeypatch):
    # First call yields a tool call; the stored assistant turn must carry it forward.
    chunks = [SimpleNamespace(usage=None, choices=[
        _choice(tool_calls=[_tc(0, id="c1", name="get_x", arguments='{}')], finish_reason="tool_calls")])]
    client, _ = _client(monkeypatch, chunks)
    client.add_user_message("go")
    client.call_model_api()

    assistant = client.messages[-1]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["id"] == "c1"
    assert assistant["tool_calls"][0]["function"]["name"] == "get_x"


def test_cost_uses_registry_pricing(monkeypatch):
    chunks = [SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500,
                              prompt_tokens_details=SimpleNamespace(cached_tokens=200)),
        choices=[_choice(content="x", finish_reason="stop")])]
    client, _ = _client(monkeypatch, chunks)
    resp = client.call_model_api()
    cost = client._estimate_cost(resp.usage)
    assert cost["cache_read_tokens"] == 200
    assert cost["total_usd"] > 0
