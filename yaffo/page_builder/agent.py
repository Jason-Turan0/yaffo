"""Page-builder agent loop.

Drives a ModelClient through a tool-use loop: add the user turn, call the model,
execute any tool calls via the registered ToolProviders, feed results back, and
repeat until the model stops calling tools (or a cap is hit). Widgets are created
and edited as a side effect of the model's tool calls (server-side), so the
"result" is the mutated page plus the assistant's closing message.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator, Optional

from sqlalchemy.orm import Session

from yaffo.page_builder.model_clients import (
    AnthropicModelClient,
    ModelAlias,
    ModelClient,
    ToolCall,
    ToolCallResult,
)
from yaffo.page_builder.prompt_generator import build_system_prompt
from yaffo.page_builder.tool_providers import (
    ContentBlock,
    DataQueryToolProvider,
    ToolProvider,
    ToolResult,
    WidgetTemplateToolProvider,
    WidgetToolProvider,
    to_anthropic_tools,
)
from pathlib import Path

@dataclass
class AgentResult:
    text: str  # the assistant's closing message to the user
    iterations: int
    tool_calls: int
    stop_reason: Optional[str]
    ok: bool


@dataclass
class AgentEvent:
    """One step the agent took, streamed to the caller as it happens.

    `type` is one of:
      - "assistant": the model produced text this turn (`text`).
      - "tool":      a tool finished (`name`, `tool_input`, `result`, `is_error`).
      - "done":      the model stopped without calling tools (`stop_reason`).
      - "error":     the run failed or hit a cap (`text` describes it).
      - "cancelled": a cancel was requested between iterations and the loop stopped.
    """
    type: str
    text: str = ""
    name: Optional[str] = None
    tool_input: Optional[dict] = None
    result: str = ""
    tool_result_data: Optional[dict] = None  # tool's structured host payload (e.g. a widget)
    is_error: bool = False
    stop_reason: Optional[str] = None


class PageBuilderAgent:
    def __init__(
        self,
        client: ModelClient,
        tool_providers: list[ToolProvider],
        max_iterations: int = 25,
    ):
        self.client = client
        self.tool_providers = tool_providers
        self.max_iterations = max_iterations
        self._provider_by_tool: dict[str, ToolProvider] = {}
        for provider in tool_providers:
            for tool in provider.get_tools():
                if tool.name in self._provider_by_tool:
                    raise ValueError(f"Duplicate tool name: {tool.name}")
                self._provider_by_tool[tool.name] = provider

    def run_events(
        self, user_message: str, should_cancel: Optional[Callable[[], bool]] = None
    ) -> Iterator[AgentEvent]:
        """Drive the tool-use loop, yielding an AgentEvent per step (assistant
        text, each finished tool call, and a terminal done/error) so callers can
        stream progress.

        `should_cancel`, if given, is polled at the top of each iteration (before
        the expensive model call); when it returns True the loop stops with a
        terminal "cancelled" event. This is cooperative cancel at the iteration
        boundary — an in-flight model call still runs to completion (responsive
        mid-call cancel is a later, stream-level concern)."""
        self.client.add_user_message(user_message)
        iterations = 0
        try:
            while iterations < self.max_iterations:
                if should_cancel is not None and should_cancel():
                    yield AgentEvent("cancelled", text="Generation cancelled.", stop_reason="cancelled")
                    return
                iterations += 1
                response = self.client.call_model_api()
                if response is None:
                    yield AgentEvent("error", text="Generation failed.", stop_reason="error")
                    return

                # The turn was cut off at max_tokens (thinking shares this budget):
                # any text/tool_use is truncated, so surface it instead of acting on
                # a half-streamed (invalid-JSON) tool call or treating it as done.
                if response.stop_reason == "max_tokens":
                    yield AgentEvent(
                        "error",
                        text="Generation hit the output token limit and was cut off — try a simpler request.",
                        stop_reason="max_tokens",
                    )
                    return

                if response.text:
                    yield AgentEvent("assistant", text=response.text)

                if response.tool_calls:
                    results = [self._execute(call) for call in response.tool_calls]
                    for call, result in zip(response.tool_calls, results):
                        yield AgentEvent(
                            "tool",
                            name=call.name,
                            tool_input=call.input,
                            result=result.result,
                            tool_result_data=result.data,
                            is_error=result.is_error,
                        )
                    self.client.add_tool_result_message(results)
                    continue

                # No tool calls -> the model is done.
                yield AgentEvent("done", text=response.text, stop_reason=response.stop_reason)
                return

            yield AgentEvent(
                "error", text="(stopped: reached max iterations)", stop_reason="max_iterations"
            )
        finally:
            for provider in self.tool_providers:
                provider.disconnect()

    def run(self, user_message: str) -> AgentResult:
        """Synchronous wrapper over run_events: drains the stream and returns the
        aggregate result. Kept for non-streaming callers."""
        text = ""
        tool_calls = 0
        iterations = 1  # at least one model call happens before the first event
        stop_reason: Optional[str] = None
        ok = False
        for event in self.run_events(user_message):
            if event.type == "assistant":
                text = event.text
            elif event.type == "tool":
                tool_calls += 1
            elif event.type == "done":
                text = event.text or text
                stop_reason = event.stop_reason
                ok = True
            elif event.type in ("error", "cancelled"):
                text = event.text
                stop_reason = event.stop_reason
                ok = False
        return AgentResult(text, iterations, tool_calls, stop_reason, ok=ok)

    def _execute(self, call: ToolCall) -> ToolCallResult:
        provider = self._provider_by_tool.get(call.name)
        if provider is None:
            return ToolCallResult(call.id, call.name, f"No implementation for tool {call.name}", is_error=True)
        try:
            result = provider.call_tool(call.name, call.input)
            if isinstance(result, ToolResult):
                return ToolCallResult(call.id, call.name, result.model_text, data=result.host_data)
            if isinstance(result, ContentBlock):
                return ToolCallResult(call.id, call.name, result.text)
            return ToolCallResult(call.id, call.name, result)
        except Exception as exc:  # tool failures are fed back, not raised
            return ToolCallResult(call.id, call.name, f"Error: {exc}", is_error=True)


def create_agent(
    version_id: int,
    *,
    model: ModelAlias,
    api_key: str,
    session: Session,
    max_iterations: int = 25,
) -> PageBuilderAgent:
    """Wire the agent for a working version: data-query + widget tools, the stable
    system prompt, and an Anthropic client. The widget tool persists directly into
    `version_id`. `model` and `api_key` are required — the caller resolves them
    (llm_config.get_model(session) / get_api_key()), so neither this nor the client
    falls through to db.session or a global key lookup. `session` is the DB session
    the tools read/write under (a request's db.session or a worker's SessionFactory
    session)."""
    providers: list[ToolProvider] = [
        DataQueryToolProvider(session=session),
        WidgetTemplateToolProvider(),
        WidgetToolProvider(version_id, session=session),
    ]
    client = AnthropicModelClient(
        model=model,
        system_prompt=build_system_prompt(),
        tools=to_anthropic_tools(providers),
        api_key=api_key,
        log_dir= Path.cwd() / "model_logs",
    )
    return PageBuilderAgent(client, providers, max_iterations=max_iterations)