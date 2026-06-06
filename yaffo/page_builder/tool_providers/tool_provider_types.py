"""Tool-provider interface and types (ported from toolprovider.types.ts).

A ToolProvider exposes one or more tools to the agent: it advertises their
definitions (`get_tools`) and executes calls (`call_tool`). Definitions are kept
provider-agnostic (`RawToolDefinition`) and converted to the Anthropic tool shape
when building a request.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Union


@dataclass
class RawToolDefinition:
    name: str
    description: str
    input_schema: dict

    def to_anthropic_tool(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class ContentBlock:
    text: str
    type: str = "text"


CallToolReturn = Union[str, ContentBlock]


class ToolProvider(ABC):
    @abstractmethod
    def get_tools(self) -> list[RawToolDefinition]: ...

    @abstractmethod
    def call_tool(self, name: str, args: dict) -> CallToolReturn: ...

    def disconnect(self) -> None:
        return None


def to_anthropic_tools(providers: list[ToolProvider]) -> list[dict]:
    """Flatten providers' tool definitions into the Anthropic `tools` list."""
    return [tool.to_anthropic_tool() for provider in providers for tool in provider.get_tools()]