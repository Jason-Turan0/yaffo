"""Anthropic model client for the page-builder agent.

Implements the provider-agnostic `ModelClient` interface (model_client_types)
over the official `anthropic` SDK, with the patterns this app needs:
- prompt caching on the (large, stable) system prompt and the latest turn
- streaming (avoids HTTP timeouts on long widget-generation output)
- a manual message loop, so the caller drives tool execution — some tools run
  server-side, others flow out to the UI for evaluation (see the agent loop)
- filesystem logging of every API call (request, response, usage, cost)

The client is loop-agnostic: call `add_user_message()`, `call_model_api()` to get
the assistant turn, inspect `response.tool_calls`, then feed results back with
`add_tool_result_message()` and call again.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import anthropic

from yaffo.common import ROOT_DIR
from yaffo.config import get_int as get_config_int
from yaffo.logging_config import get_logger
from yaffo.site_agents.model_clients.model_client_types import (
    ModelAlias,
    ModelClient,
    ModelClientConfig,
    ModelResponse,
    ToolCall,
    ToolCallResult,
    Usage,
    to_text_part,
    to_tool_result_part,
)

# Per-1M-token pricing (input, output), keyed by the model IDs we offer.
_PRICING = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
_CACHE_WRITE_MULT = 1.25  # 5-minute TTL write premium
_CACHE_READ_MULT = 0.10

# Each agent run writes one timestamped sub-dir of per-call JSON under log_dir. Keep
# only the most recent runs so model_logs can't grow unbounded (the .log files are
# capped by RotatingFileHandler; this is the equivalent for the per-run dumps).
# Count from config.toml ([logging] max_model_log_runs), default 50.
_MAX_LOG_RUNS = get_config_int("logging", "max_model_log_runs", 50)

# Default output-token budget per model call, from config.toml ([ai] max_output_tokens).
# Floored well above zero — this is shared with the thinking budget, so a tiny value
# would cut off every generation (stop_reason=max_tokens). See the max_tokens note below.
_MAX_OUTPUT_TOKENS = get_config_int("ai", "max_output_tokens", 64000, minimum=1024)

logger = get_logger(__name__)


def _supports_adaptive_thinking(model: str) -> bool:
    return model.startswith("claude-opus-4") or model.startswith("claude-sonnet-4-6")


def _jsonable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return str(obj)


def _error_payload(error: anthropic.APIError) -> dict:
    """Serialize an Anthropic APIError's HTTP response for the log. APIStatusError
    carries the response (status, body, headers); connection/timeout errors never
    got one, so every field is read defensively with getattr."""
    payload: dict[str, Any] = {
        "type": type(error).__name__,
        "message": str(error),
        "request_id": getattr(error, "request_id", None),
        "status_code": getattr(error, "status_code", None),
        "body": getattr(error, "body", None),
    }
    response = getattr(error, "response", None)  # httpx.Response on APIStatusError
    if response is not None:
        payload["headers"] = dict(response.headers)
        if payload["body"] is None:
            try:
                payload["body"] = response.json()
            except ValueError:
                payload["body"] = response.text
    return payload


class AnthropicModelClient(ModelClient):
    def __init__(
        self,
        *,
        model: ModelAlias,
        system_prompt: str,
        tools: Optional[list[dict]] = None,
        output_schema: Optional[dict] = None,
        # Adaptive thinking draws from this same budget, so it must be generous —
        # at 8192 the model could spend the whole budget thinking and get cut off
        # (stop_reason=max_tokens) before emitting a widget. The streaming path
        # (call_model_api) avoids HTTP timeouts at this size. Opus 4.8 allows 128K.
        # Default from config.toml ([ai] max_output_tokens).
        max_tokens: int = _MAX_OUTPUT_TOKENS,
        log_dir: Optional[Path] = None,
        api_key: str,
    ):
        self.model: ModelAlias = model
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.output_schema = output_schema
        self.max_tokens = max_tokens
        self.messages: list[dict] = []
        self._client = anthropic.Anthropic(api_key=api_key)
        self.log_dir = Path(log_dir) if log_dir else (ROOT_DIR / "model_logs")
        self._call_count = 0
        self.task_start = datetime.now()
        self._prune_old_runs()

    def _prune_old_runs(self) -> None:
        """Keep only the newest _MAX_LOG_RUNS run sub-dirs under log_dir; delete the
        rest. Run dirs are named by start timestamp, so a lexical sort is chronological.
        Best-effort — a filesystem error here must never break a model call."""
        try:
            if not self.log_dir.exists():
                return
            run_dirs = sorted((d for d in self.log_dir.iterdir() if d.is_dir()))
            for stale in run_dirs[:-_MAX_LOG_RUNS]:
                shutil.rmtree(stale, ignore_errors=True)
        except OSError:
            pass

    @classmethod
    def from_config(cls, config: ModelClientConfig, **kwargs: Any) -> "AnthropicModelClient":
        return cls(
            model=config.model,
            system_prompt=config.system_prompt,
            tools=config.tools,
            output_schema=config.output_schema,
            log_dir=Path(config.run_log_dir),
            **kwargs,
        )

    # ---- conversation building ------------------------------------------

    def add_user_message(self, content: Any) -> None:
        if isinstance(content, str):
            content = [to_text_part(content)]
        self.messages.append({"role": "user", "content": content})

    def add_tool_result_message(self, results: list[ToolCallResult]) -> None:
        self.messages.append(
            {"role": "user", "content": [to_tool_result_part(r) for r in results]}
        )

    def set_system_prompt(self, prompt: str) -> None:
        self.system_prompt = prompt

    def set_output_schema(self, schema: dict) -> None:
        self.output_schema = schema

    # ---- the call -------------------------------------------------------

    def call_model_api(self) -> Optional[ModelResponse]:
        params = self._build_params()
        timestamp = datetime.now()
        started = time.monotonic()
        message: Optional[anthropic.types.Message] = None
        error: Optional[anthropic.APIError] = None
        try:
            with self._client.messages.stream(**params) as stream:
                message = stream.get_final_message()
            # Append the full assistant content (incl. thinking/tool_use blocks)
            # so the next turn preserves it.
            self.messages.append({"role": "assistant", "content": message.content})
            response = self._to_response(message)
            if response.text:
                logger.debug("🤖 %s", response.text[:100])
            return response
        except anthropic.APIError as e:
            logger.error("Anthropic API call failed: %s", e)
            error = e
            return None
        finally:
            # Log the raw request + raw response: the SDK message on success, or the
            # error's HTTP response (status / body / headers) on failure.
            self._write_log(timestamp, (time.monotonic() - started) * 1000, params, message, error)

    def _to_response(self, message: anthropic.types.Message) -> ModelResponse:
        text = "".join(b.text for b in message.content if b.type == "text")
        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=b.input)
            for b in message.content
            if b.type == "tool_use"
        ]
        return ModelResponse(
            text=text,
            stop_reason=message.stop_reason,
            tool_calls=tool_calls,
            usage=self._usage(message.usage),
            message=message,
        )

    def _build_params(self) -> dict:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [
                {"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": self._messages_with_cache(),
        }
        if self.tools:
            params["tools"] = self.tools
        if self.output_schema:
            params["output_config"] = {
                "format": {"type": "json_schema", "schema": self.output_schema}
            }
        if _supports_adaptive_thinking(self.model):
            params["thinking"] = {"type": "adaptive"}
        return params

    def _messages_with_cache(self) -> list[dict]:
        """Cache the conversation prefix by marking the last block of the latest
        (user/tool) turn. The system prompt carries its own breakpoint."""
        msgs = list(self.messages)
        if msgs:
            last = msgs[-1]
            content = last.get("content")
            if isinstance(content, list) and content and isinstance(content[-1], dict):
                marked = [dict(b) for b in content]
                marked[-1] = {**marked[-1], "cache_control": {"type": "ephemeral"}}
                msgs[-1] = {**last, "content": marked}
        return msgs

    # ---- logging --------------------------------------------------------

    @staticmethod
    def _usage(usage: Any) -> Usage:
        return Usage(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )

    def _estimate_cost(self, usage: Usage) -> Optional[dict]:
        price = _PRICING.get(self.model)
        if price is None:
            return None
        in_rate, out_rate = (p / 1_000_000 for p in price)
        total = (
            usage.input_tokens * in_rate
            + usage.output_tokens * out_rate
            + usage.cache_write_tokens * in_rate * _CACHE_WRITE_MULT
            + usage.cache_read_tokens * in_rate * _CACHE_READ_MULT
        )
        return {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "total_usd": round(total, 6),
        }

    def _write_log(
        self,
        timestamp: datetime,
        duration_ms: float,
        params: dict,
        message: Any,
        error: Optional[anthropic.APIError] = None,
    ) -> None:
        if logger.getEffectiveLevel() > logging.DEBUG:
            return
        self._call_count += 1
        usage = self._usage(message.usage) if message is not None else None
        if message is not None:
            response: Any = message.model_dump()
        elif error is not None:
            response = _error_payload(error)
        else:
            response = None
        record = {
            "timestamp": timestamp.isoformat(timespec="seconds"),
            "call": self._call_count,
            "model": self.model,
            "duration_ms": round(duration_ms),
            "success": message is not None,
            "request": params,
            "response": response,
            "cost": self._estimate_cost(usage) if usage is not None else None,
        }
        request_log_dir =self.log_dir / f"{self.task_start:%Y%m%d-%H%M%S}"
        file_path = request_log_dir / f"{self._call_count:03d}.json"
        try:
            request_log_dir.mkdir(parents=True, exist_ok=True)
            file_path.write_text(json.dumps(record, indent=2, default=_jsonable))
        except OSError:
            pass