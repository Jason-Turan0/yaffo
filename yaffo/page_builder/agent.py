"""Page-builder agent loop.

Drives a ModelClient through a tool-use loop: add the user turn, call the model,
execute any tool calls via the registered ToolProviders, feed results back, and
repeat until the model stops calling tools (or a cap is hit). Widgets are created
and edited as a side effect of the model's tool calls (server-side), so the
"result" is the mutated page plus the assistant's closing message.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from yaffo.page_builder.model_clients import (
    AnthropicModelClient,
    ModelClient,
    ToolCall,
    ToolCallResult,
)
from yaffo.page_builder.prompt_generator import build_system_prompt
from yaffo.page_builder.tool_providers import (
    ContentBlock,
    DataQueryToolProvider,
    ToolProvider,
    WidgetToolProvider,
    to_anthropic_tools,
)


@dataclass
class AgentResult:
    text: str  # the assistant's closing message to the user
    iterations: int
    tool_calls: int
    stop_reason: Optional[str]
    ok: bool


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

    def run(self, user_message: str) -> AgentResult:
        self.client.add_user_message(user_message)
        tool_calls = 0
        stop_reason: Optional[str] = None
        iterations = 0
        try:
            while iterations < self.max_iterations:
                iterations += 1
                response = self.client.call_model_api()
                if response is None:
                    return AgentResult("", iterations, tool_calls, "error", ok=False)

                stop_reason = response.stop_reason
                if response.tool_calls:
                    results = [self._execute(call) for call in response.tool_calls]
                    tool_calls += len(results)
                    self.client.add_tool_result_message(results)
                    continue

                # No tool calls -> the model is done.
                return AgentResult(response.text, iterations, tool_calls, stop_reason, ok=True)

            return AgentResult(
                "(stopped: reached max iterations)", iterations, tool_calls, "max_iterations", ok=False
            )
        finally:
            for provider in self.tool_providers:
                provider.disconnect()

    def _execute(self, call: ToolCall) -> ToolCallResult:
        provider = self._provider_by_tool.get(call.name)
        if provider is None:
            return ToolCallResult(call.id, call.name, f"No implementation for tool {call.name}", is_error=True)
        try:
            result = provider.call_tool(call.name, call.input)
            text = result.text if isinstance(result, ContentBlock) else result
            return ToolCallResult(call.id, call.name, text)
        except Exception as exc:  # tool failures are fed back, not raised
            return ToolCallResult(call.id, call.name, f"Error: {exc}", is_error=True)


def create_agent(
    page_id: int,
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    max_iterations: int = 25,
) -> PageBuilderAgent:
    """Wire the default agent for a page: data-query + widget tools, the stable
    system prompt, and an Anthropic client."""
    providers: list[ToolProvider] = [DataQueryToolProvider(), WidgetToolProvider(page_id)]
    client = AnthropicModelClient(
        model=model,
        system_prompt=build_system_prompt(),
        tools=to_anthropic_tools(providers),
        api_key=api_key,
    )
    return PageBuilderAgent(client, providers, max_iterations=max_iterations)