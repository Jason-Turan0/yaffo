"""OpenAI-compatible model client for the page-builder agent.

One implementation of the `ModelClient` interface that speaks the OpenAI Chat
Completions schema — shared by every vendor that exposes it (OpenAI, Grok, Kimi,
DeepSeek, and Gemini via its compat endpoint). They differ only by `base_url` +
API key + model id + pricing, all of which come from `providers.py`; this class is
vendor-agnostic.

Mirrors `AnthropicModelClient`: streaming (avoids HTTP timeouts on long output), a
manual message loop the caller drives, the same `ModelResponse` shape, and the same
per-call logging via `CallLogger`. Two schema differences are handled here so the
agent loop is unchanged: tool-call arguments arrive as a JSON *string* (parsed back
to a dict for `ToolCall.input`), and the token-limit `finish_reason` is `"length"`
(normalized to Anthropic's `"max_tokens"`, which the agent checks for).
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import openai

from yaffo.config import get_int as get_config_int
from yaffo.logging_config import get_logger
from yaffo.site_agents.model_clients.call_log import CallLogger
from yaffo.site_agents.model_clients.providers import pricing_for
from yaffo.site_agents.model_clients.model_client_types import (
    ModelClient,
    ModelResponse,
    ToolCall,
    ToolCallResult,
    Usage,
)

logger = get_logger(__name__)

_MAX_OUTPUT_TOKENS = get_config_int("ai", "max_output_tokens", 64000, minimum=1024)

# OpenAI's newer (reasoning) models reject the legacy `max_tokens` and require
# `max_completion_tokens`; the other compat vendors take `max_tokens`. Keyed by provider.
_MAX_TOKENS_PARAM = {"openai": "max_completion_tokens"}

# OpenAI -> Anthropic finish-reason names, for the fields the agent loop inspects.
_STOP_REASON = {"length": "max_tokens", "tool_calls": "tool_use", "stop": "end_turn"}


class OpenAICompatibleModelClient(ModelClient):
    def __init__(
        self,
        *,
        model: str,
        provider_id: str,
        base_url: str,
        system_prompt: str,
        tools: Optional[list[dict]] = None,
        output_schema: Optional[dict] = None,
        max_tokens: int = _MAX_OUTPUT_TOKENS,
        log_dir: Optional[Path] = None,
        api_key: str,
    ):
        self.model = model
        self.provider_id = provider_id
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.output_schema = output_schema
        self.max_tokens = max_tokens
        # System prompt is the first message; the rest of the turns append after it.
        self.messages: list[dict] = []
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self._log = CallLogger(log_dir)

    # ---- conversation building ------------------------------------------

    def add_user_message(self, content: Any) -> None:
        # The agent passes a plain string; accept the neutral text-part list too.
        if not isinstance(content, str):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        self.messages.append({"role": "user", "content": content})

    def add_tool_result_message(self, results: list[ToolCallResult]) -> None:
        # OpenAI takes one {role:"tool"} message per result (not a single user turn).
        for r in results:
            self.messages.append({
                "role": "tool",
                "tool_call_id": r.tool_call_id,
                "content": r.result,
            })

    def set_system_prompt(self, prompt: str) -> None:
        self.system_prompt = prompt

    def set_output_schema(self, schema: dict) -> None:
        self.output_schema = schema

    # ---- the call -------------------------------------------------------

    def call_model_api(self) -> Optional[ModelResponse]:
        params = self._build_params()
        timestamp = datetime.now()
        started = time.monotonic()
        response: Optional[ModelResponse] = None
        error: Optional[openai.OpenAIError] = None
        try:
            response = self._stream(params)
            self.messages.append(self._assistant_message(response))
            if response.text:
                logger.debug("🤖 %s", response.text[:100])
            return response
        except openai.OpenAIError as e:
            logger.error("%s API call failed: %s", self.provider_id, e)
            error = e
            return None
        finally:
            self._write_log(timestamp, (time.monotonic() - started) * 1000, params, response, error)

    def _stream(self, params: dict) -> ModelResponse:
        """Consume the streamed chunks into a ModelResponse: concatenate text deltas
        and tool-call fragments (arguments arrive in pieces), then parse each tool
        call's JSON-string arguments into a dict."""
        text_parts: list[str] = []
        # index -> {id, name, args}; tool-call fragments are keyed by their position.
        tool_frags: dict[int, dict[str, str]] = {}
        finish_reason: Optional[str] = None
        usage: Any = None

        with self._client.chat.completions.create(**params) as stream:
            for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if getattr(delta, "content", None):
                    text_parts.append(delta.content)
                for tc in getattr(delta, "tool_calls", None) or []:
                    frag = tool_frags.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        frag["id"] = tc.id
                    if tc.function and tc.function.name:
                        frag["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        frag["args"] += tc.function.arguments
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

        tool_calls = [
            ToolCall(id=f["id"], name=f["name"], input=_parse_args(f["args"]))
            for _, f in sorted(tool_frags.items())
        ]
        return ModelResponse(
            text="".join(text_parts),
            stop_reason=_STOP_REASON.get(finish_reason or "", finish_reason),
            tool_calls=tool_calls,
            usage=self._usage(usage),
            message={"finish_reason": finish_reason, "tool_frags": tool_frags},
        )

    def _assistant_message(self, response: ModelResponse) -> dict:
        """The assistant turn in OpenAI shape, stored so the next request replays it.
        When there are tool calls, content may be empty but the tool_calls must carry
        their original JSON-string arguments."""
        msg: dict[str, Any] = {"role": "assistant", "content": response.text or None}
        if response.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
                }
                for tc in response.tool_calls
            ]
        return msg

    def _build_params(self) -> dict:
        params: dict[str, Any] = {
            "model": self.model,
            _MAX_TOKENS_PARAM.get(self.provider_id, "max_tokens"): self.max_tokens,
            "messages": [{"role": "system", "content": self.system_prompt}, *self.messages],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.tools:
            params["tools"] = self.tools
        if self.output_schema:
            # Best-effort structured output; the agents don't currently set a schema,
            # and support varies across compat vendors.
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": self.output_schema},
            }
        return params

    # ---- logging --------------------------------------------------------

    @staticmethod
    def _usage(usage: Any) -> Usage:
        if usage is None:
            return Usage()
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) if details else 0
        return Usage(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cache_write_tokens=0,  # OpenAI-family caching is read-only (no write premium)
            cache_read_tokens=cached or 0,
        )

    def _estimate_cost(self, usage: Usage) -> Optional[dict]:
        pricing = pricing_for(self.model)
        if pricing is None:
            return None
        in_rate = pricing.input / 1_000_000
        out_rate = pricing.output / 1_000_000
        cached_rate = pricing.cached_input / 1_000_000
        # prompt_tokens includes the cached tokens; the uncached remainder bills at the
        # full input rate, the cached portion at the discounted rate.
        uncached = max(0, usage.input_tokens - usage.cache_read_tokens)
        total = uncached * in_rate + usage.cache_read_tokens * cached_rate + usage.output_tokens * out_rate
        return {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "total_usd": round(total, 6),
        }

    def _write_log(
        self,
        timestamp: datetime,
        duration_ms: float,
        params: dict,
        response: Optional[ModelResponse],
        error: Optional[openai.OpenAIError],
    ) -> None:
        payload: Any
        if response is not None:
            payload = {
                "text": response.text,
                "stop_reason": response.stop_reason,
                "tool_calls": [{"id": t.id, "name": t.name, "input": t.input} for t in response.tool_calls],
            }
        elif error is not None:
            payload = {"type": type(error).__name__, "message": str(error),
                       "status_code": getattr(error, "status_code", None),
                       "body": getattr(error, "body", None)}
        else:
            payload = None
        self._log.write(
            model=self.model,
            timestamp=timestamp,
            duration_ms=duration_ms,
            success=response is not None,
            request=params,
            response=payload,
            cost=self._estimate_cost(response.usage) if response is not None else None,
        )


def _parse_args(raw: str) -> dict:
    """Tool-call arguments are a JSON string in this schema; parse to the dict the
    agent expects. A truncated/empty payload (e.g. a cut-off turn) yields {}."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
