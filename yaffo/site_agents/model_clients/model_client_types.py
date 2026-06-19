"""Generic model-client types (ported from the TS model_client.interface).

Provider-agnostic shapes so the page-builder agent can be written against an
interface rather than the Anthropic SDK directly. Only Anthropic models are
listed in `ModelAlias` for now.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

# Only Anthropic models for now (keep in sync with llm_config.AVAILABLE_MODELS).
ModelAlias = Literal[
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]

ModelProvider = Literal["anthropic"]


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class ToolCallResult:
    tool_call_id: str
    tool_name: str
    result: str  # text shown to the model
    is_error: bool = False
    data: Optional[dict] = None  # structured payload for the host (not the model)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass
class ModelResponse:
    text: str
    stop_reason: Optional[str]
    tool_calls: list[ToolCall]
    usage: Usage
    message: Any  # the raw provider assistant message


@dataclass
class ModelClientConfig:
    run_log_dir: str
    model: ModelAlias
    system_prompt: str
    tools: list[dict] = field(default_factory=list)
    output_schema: Optional[dict] = None


def to_text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def to_tool_result_part(result: ToolCallResult) -> dict:
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": result.tool_call_id,
        "content": result.result,
    }
    if result.is_error:
        block["is_error"] = True
    return block


class ModelClient(ABC):
    model: ModelAlias

    @abstractmethod
    def add_user_message(self, content: list[dict]) -> None: ...

    @abstractmethod
    def add_tool_result_message(self, results: list[ToolCallResult]) -> None: ...

    @abstractmethod
    def call_model_api(self) -> Optional[ModelResponse]: ...

    @abstractmethod
    def set_system_prompt(self, prompt: str) -> None: ...

    @abstractmethod
    def set_output_schema(self, schema: dict) -> None: ...