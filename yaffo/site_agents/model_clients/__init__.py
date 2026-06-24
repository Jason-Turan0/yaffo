"""Model clients for the page-builder agent (provider-agnostic interface +
Anthropic and OpenAI-compatible implementations)."""
from yaffo.site_agents.model_clients.model_client_types import (
    ModelAlias,
    ModelClient,
    ModelClientConfig,
    ModelProvider,
    ModelResponse,
    ToolCall,
    ToolCallResult,
    Usage,
    to_text_part,
    to_tool_result_part,
)
from yaffo.site_agents.model_clients.model_client import AnthropicModelClient
from yaffo.site_agents.model_clients.openai_client import OpenAICompatibleModelClient
from yaffo.site_agents.model_clients.factory import create_model_client

__all__ = [
    "ModelAlias",
    "ModelClient",
    "ModelClientConfig",
    "ModelProvider",
    "ModelResponse",
    "ToolCall",
    "ToolCallResult",
    "Usage",
    "to_text_part",
    "to_tool_result_part",
    "AnthropicModelClient",
    "OpenAICompatibleModelClient",
    "create_model_client",
]