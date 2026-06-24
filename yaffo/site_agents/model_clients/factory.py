"""Picks the right ModelClient for a selected model.

The one place that maps a model id -> provider -> client: Anthropic models get the
native `AnthropicModelClient`; everything else gets the `OpenAICompatibleModelClient`
pointed at the provider's `base_url`. Tool definitions are converted to the matching
schema here, so callers just pass the neutral `ToolProvider`s.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from yaffo.site_agents.model_clients.model_client import AnthropicModelClient
from yaffo.site_agents.model_clients.model_client_types import ModelClient
from yaffo.site_agents.model_clients.openai_client import OpenAICompatibleModelClient
from yaffo.site_agents.model_clients.providers import provider_for_model
from yaffo.site_agents.tool_providers.tool_provider_types import (
    ToolProvider,
    to_anthropic_tools,
    to_openai_tools,
)


def create_model_client(
    *,
    model: str,
    system_prompt: str,
    providers: list[ToolProvider],
    api_key: str,
    log_dir: Optional[Path] = None,
    **kwargs: Any,
) -> ModelClient:
    provider = provider_for_model(model)
    if provider is None:
        raise ValueError(f"Unknown model id: {model!r}")
    if provider.base_url is None:  # Anthropic native SDK
        return AnthropicModelClient(
            model=model,
            system_prompt=system_prompt,
            tools=to_anthropic_tools(providers),
            api_key=api_key,
            log_dir=log_dir,
            **kwargs,
        )
    return OpenAICompatibleModelClient(
        model=model,
        provider_id=provider.id,
        base_url=provider.base_url,
        system_prompt=system_prompt,
        tools=to_openai_tools(providers),
        api_key=api_key,
        log_dir=log_dir,
        **kwargs,
    )
