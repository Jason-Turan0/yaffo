"""Unit tests for the page-builder agent loop (yaffo/page_builder/agent.py).

The ModelClient is stubbed so it (a) records every message the agent hands it and
(b) replays scripted responses, letting us assert exactly what the agent sends to
the model and how it reacts -- without any network or provider SDK. Tool providers
are stubbed too, so tool dispatch / error handling is exercised in isolation.
"""
import pytest

from yaffo.page_builder.agent import Agent
from yaffo.page_builder.model_clients import (
    ModelClient,
    ModelResponse,
    ToolCall,
    ToolCallResult,
    Usage,
)
from yaffo.page_builder.tool_providers import (
    RawToolDefinition,
    ToolProvider,
    ToolResult,
)

pytestmark = pytest.mark.unit

USER_MSG = "make a gallery"


# --- stubs -----------------------------------------------------------------

class StubModelClient(ModelClient):
    """Records every message the agent sends and replays scripted responses."""
    model = "claude-sonnet-4-6"

    def __init__(self, responses):
        self._responses = list(responses)
        self.call_count = 0
        self.user_messages = []                # each add_user_message arg
        self.tool_result_batches = []          # each add_tool_result_message arg (a list)

    def add_user_message(self, content):
        self.user_messages.append(content)

    def add_tool_result_message(self, results):
        self.tool_result_batches.append(results)

    def call_model_api(self):
        # IndexError here means the agent called the model more than scripted.
        response = self._responses[self.call_count]
        self.call_count += 1
        return response

    def set_system_prompt(self, prompt):
        pass

    def set_output_schema(self, schema):
        pass


class StubToolProvider(ToolProvider):
    """Exposes one named tool, records calls, and counts disconnects."""

    def __init__(self, name="stub_tool", result="ok", raises=None):
        self._name = name
        self._result = result
        self._raises = raises
        self.calls = []
        self.disconnects = 0

    def get_tools(self):
        return [RawToolDefinition(self._name, "A stub tool.", {"type": "object"})]

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if self._raises is not None:
            raise self._raises
        return self._result

    def disconnect(self):
        self.disconnects += 1


def _resp(text="", tool_calls=None, stop_reason="end_turn"):
    return ModelResponse(
        text=text, stop_reason=stop_reason,
        tool_calls=tool_calls or [], usage=Usage(), message=None,
    )


def _call(name="stub_tool", inp=None, id="call-1"):
    return ToolCall(id=id, name=name, input=inp or {})


def _events(agent):
    return list(agent.run_events(USER_MSG))


# --- what gets sent to the model -------------------------------------------

class TestUserMessage:
    def test_user_message_is_forwarded_to_the_client(self):
        client = StubModelClient([_resp(text="hi")])
        agent = Agent(client, [StubToolProvider()])
        _events(agent)
        assert client.user_messages == [USER_MSG]
        assert client.call_count == 1


class TestToolResultsSentBack:
    def test_tool_result_is_sent_back_to_the_model(self):
        provider = StubToolProvider(result="ok")
        client = StubModelClient([
            _resp(tool_calls=[_call(inp={"q": 1})]),  # turn 1: one tool call
            _resp(text="done"),                        # turn 2: model finishes
        ])
        agent = Agent(client, [provider])
        _events(agent)

        assert client.call_count == 2
        assert provider.calls == [("stub_tool", {"q": 1})]
        # Exactly one tool-result message, carrying one result, fed back verbatim.
        assert len(client.tool_result_batches) == 1
        (result,) = client.tool_result_batches[0]
        assert (result.tool_call_id, result.tool_name, result.result, result.is_error) == (
            "call-1", "stub_tool", "ok", False,
        )

    def test_model_receives_model_text_host_keeps_data(self):
        # A ToolResult addresses two audiences: the model sees model_text, the host
        # (event stream) gets host_data. They must not bleed into each other.
        provider = StubToolProvider(result=ToolResult(model_text="for-model", host_data={"widget": "x"}))
        client = StubModelClient([_resp(tool_calls=[_call()]), _resp(text="done")])
        agent = Agent(client, [provider])
        events = _events(agent)

        (result,) = client.tool_result_batches[0]
        assert result.result == "for-model"
        assert result.data == {"widget": "x"}
        tool_event = next(e for e in events if e.type == "tool")
        assert tool_event.tool_result_data == {"widget": "x"}

    def test_multiple_tool_calls_go_back_in_one_batch_in_order(self):
        provider = StubToolProvider()
        client = StubModelClient([
            _resp(tool_calls=[_call(id="call-1", inp={"n": 1}), _call(id="call-2", inp={"n": 2})]),
            _resp(text="done"),
        ])
        agent = Agent(client, [provider])
        _events(agent)

        assert provider.calls == [("stub_tool", {"n": 1}), ("stub_tool", {"n": 2})]
        assert len(client.tool_result_batches) == 1
        batch = client.tool_result_batches[0]
        assert [r.tool_call_id for r in batch] == ["call-1", "call-2"]

    def test_unknown_tool_is_reported_back_as_an_error_result(self):
        provider = StubToolProvider(name="stub_tool")
        client = StubModelClient([
            _resp(tool_calls=[_call(name="ghost")]),  # no provider handles 'ghost'
            _resp(text="done"),
        ])
        agent = Agent(client, [provider])
        _events(agent)

        assert provider.calls == []  # never reached the registered tool
        (result,) = client.tool_result_batches[0]
        assert result.is_error is True
        assert "No implementation for tool ghost" in result.result

    def test_tool_exception_is_fed_back_not_raised(self):
        provider = StubToolProvider(raises=ValueError("boom"))
        client = StubModelClient([_resp(tool_calls=[_call()]), _resp(text="done")])
        agent = Agent(client, [provider])
        events = _events(agent)  # must not raise

        (result,) = client.tool_result_batches[0]
        assert result.is_error is True
        assert "Error: boom" in result.result
        # The loop continued and the model still finished.
        assert any(e.type == "done" for e in events)


# --- termination -----------------------------------------------------------

class TestTermination:
    def test_none_response_yields_error_and_sends_no_tool_results(self):
        client = StubModelClient([None])
        agent = Agent(client, [StubToolProvider()])
        events = _events(agent)

        assert client.user_messages == [USER_MSG]
        assert client.call_count == 1
        assert client.tool_result_batches == []
        assert events[-1].type == "error"
        assert events[-1].text == "Generation failed."

    def test_max_tokens_truncation_yields_error_without_acting(self):
        # The turn was cut off (thinking ate the budget): a truncated tool_use must
        # not be executed, and the run must not be reported as a clean done.
        provider = StubToolProvider()
        client = StubModelClient([_resp(tool_calls=[_call()], stop_reason="max_tokens")])
        agent = Agent(client, [provider])
        events = _events(agent)

        assert provider.calls == []  # the truncated tool call was not executed
        assert client.tool_result_batches == []
        assert [e.type for e in events] == ["error"]
        assert events[-1].stop_reason == "max_tokens"
        assert "token limit" in events[-1].text

    def test_max_iterations_caps_the_loop(self):
        provider = StubToolProvider()
        # Always returns a tool call -> the loop would never end on its own.
        client = StubModelClient([_resp(tool_calls=[_call()]), _resp(tool_calls=[_call()])])
        agent = Agent(client, [provider], max_iterations=2)
        events = _events(agent)

        assert client.call_count == 2
        assert len(client.tool_result_batches) == 2
        assert events[-1].type == "error"
        assert events[-1].stop_reason == "max_iterations"


# --- cancellation ----------------------------------------------------------

class TestCancellation:
    def test_cancel_before_first_call_makes_no_model_call(self):
        provider = StubToolProvider()
        client = StubModelClient([_resp(text="hi")])
        agent = Agent(client, [provider])
        events = list(agent.run_events(USER_MSG, should_cancel=lambda: True))

        assert client.call_count == 0  # the model was never called
        assert [e.type for e in events] == ["cancelled"]
        assert events[-1].stop_reason == "cancelled"
        assert provider.disconnects == 1  # cleanup still ran

    def test_cancel_between_iterations_stops_before_the_next_model_call(self):
        provider = StubToolProvider()
        # Always returns a tool call -> the loop would never end on its own.
        client = StubModelClient([_resp(tool_calls=[_call()]), _resp(tool_calls=[_call()])])
        checks = {"n": 0}

        def should_cancel():
            # False at the top of iteration 1, True at the top of iteration 2.
            checks["n"] += 1
            return checks["n"] > 1

        agent = Agent(client, [provider], max_iterations=10)
        events = list(agent.run_events(USER_MSG, should_cancel=should_cancel))

        assert client.call_count == 1  # only the first iteration reached the model
        assert events[-1].type == "cancelled"

    def test_no_cancel_callback_runs_normally(self):
        client = StubModelClient([_resp(text="all done")])
        agent = Agent(client, [StubToolProvider()])
        events = list(agent.run_events(USER_MSG))  # should_cancel defaults to None
        assert [e.type for e in events] == ["assistant", "done"]


# --- provider lifecycle & wiring -------------------------------------------

class TestProviderLifecycle:
    def test_disconnect_called_after_a_normal_run(self):
        provider = StubToolProvider()
        agent = Agent(StubModelClient([_resp(text="hi")]), [provider])
        _events(agent)
        assert provider.disconnects == 1

    def test_disconnect_called_even_when_generation_fails(self):
        provider = StubToolProvider()
        agent = Agent(StubModelClient([None]), [provider])
        _events(agent)
        assert provider.disconnects == 1


class TestDuplicateToolNames:
    def test_duplicate_tool_name_across_providers_raises(self):
        with pytest.raises(ValueError, match="Duplicate tool name"):
            Agent(
                StubModelClient([]),
                [StubToolProvider(name="dup"), StubToolProvider(name="dup")],
            )


# --- run() aggregate wrapper -----------------------------------------------

class TestRunWrapper:
    def test_run_aggregates_a_successful_loop(self):
        client = StubModelClient([_resp(tool_calls=[_call()]), _resp(text="all set")])
        agent = Agent(client, [StubToolProvider()])
        result = agent.run(USER_MSG)

        assert result.ok is True
        assert result.tool_calls == 1
        assert result.text == "all set"
        assert result.stop_reason == "end_turn"

    def test_run_reports_failure(self):
        agent = Agent(StubModelClient([None]), [StubToolProvider()])
        result = agent.run(USER_MSG)

        assert result.ok is False
        assert result.stop_reason == "error"
        assert result.text == "Generation failed."