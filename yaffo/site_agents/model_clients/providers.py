"""Provider + model registry — the single source of truth for which vendors and
models the page-builder agent offers, and how to reach/price each one.

Adding a vendor or model is one entry here. Four of the five non-Anthropic vendors
(OpenAI, Grok, Kimi, DeepSeek) and Gemini's compat endpoint all speak the OpenAI
Chat Completions schema, so they share one client (openai_client.OpenAICompatibleModelClient)
and differ only by the fields below (base_url, key, models, pricing). Anthropic keeps
its own SDK/client and has base_url=None.

Pricing is per 1M tokens (USD). The non-Anthropic numbers and model ids are seeded to
current flagships and are *approximate* — confirm/adjust per vendor; nothing downstream
depends on the exact values except the cost figure in the model logs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    env_var: str       # env var that overrides the keychain (and primes spawn workers)
    keychain_key: str   # keyring entry name under the "yaffo" service
    base_url: Optional[str]  # None => the provider's native SDK (Anthropic); else OpenAI-compat


@dataclass(frozen=True)
class ModelPricing:
    input: float          # per 1M input tokens
    output: float         # per 1M output tokens
    cached_input: float   # per 1M cached/prompt-cache-read input tokens


@dataclass(frozen=True)
class Model:
    id: str
    label: str
    provider_id: str
    pricing: ModelPricing


PROVIDERS: list[Provider] = [
    Provider("anthropic", "Anthropic", "ANTHROPIC_API_KEY", "anthropic_api_key", None),
    Provider("openai", "OpenAI", "OPENAI_API_KEY", "openai_api_key", "https://api.openai.com/v1"),
    Provider("grok", "Grok (x.ai)", "XAI_API_KEY", "xai_api_key", "https://api.x.ai/v1"),
    Provider("gemini", "Google Gemini", "GEMINI_API_KEY", "gemini_api_key",
             "https://generativelanguage.googleapis.com/v1beta/openai/"),
    Provider("kimi", "Kimi (Moonshot)", "MOONSHOT_API_KEY", "moonshot_api_key", "https://api.moonshot.ai/v1"),
    Provider("deepseek", "DeepSeek", "DEEPSEEK_API_KEY", "deepseek_api_key", "https://api.deepseek.com/v1"),
]

MODELS: list[Model] = [
    # Anthropic (unchanged from the prior AVAILABLE_MODELS + local _PRICING).
    Model("claude-opus-4-8", "Claude Opus 4.8 — most capable", "anthropic", ModelPricing(5.0, 25.0, 0.5)),
    Model("claude-sonnet-4-6", "Claude Sonnet 4.6 — balanced", "anthropic", ModelPricing(3.0, 15.0, 0.3)),
    Model("claude-haiku-4-5-20251001", "Claude Haiku 4.5 — fastest", "anthropic", ModelPricing(1.0, 5.0, 0.1)),
    # One flagship per other vendor. Ids + pricing are seeds — confirm per vendor.
    Model("gpt-5.1", "GPT-5.1 (OpenAI)", "openai", ModelPricing(1.25, 10.0, 0.125)),
    Model("grok-4", "Grok 4 (x.ai)", "grok", ModelPricing(3.0, 15.0, 0.75)),
    Model("gemini-2.5-pro", "Gemini 2.5 Pro", "gemini", ModelPricing(1.25, 10.0, 0.31)),
    Model("kimi-k2-0711-preview", "Kimi K2 (Moonshot)", "kimi", ModelPricing(0.6, 2.5, 0.15)),
    Model("deepseek-chat", "DeepSeek V3", "deepseek", ModelPricing(0.27, 1.1, 0.07)),
]

_PROVIDERS_BY_ID = {p.id: p for p in PROVIDERS}
_MODELS_BY_ID = {m.id: m for m in MODELS}


def models() -> list[Model]:
    return list(MODELS)


def model_ids() -> set[str]:
    return set(_MODELS_BY_ID)


def get_model(model_id: str) -> Optional[Model]:
    return _MODELS_BY_ID.get(model_id)


def get_provider(provider_id: str) -> Optional[Provider]:
    return _PROVIDERS_BY_ID.get(provider_id)


def provider_for_model(model_id: str) -> Optional[Provider]:
    model = _MODELS_BY_ID.get(model_id)
    return _PROVIDERS_BY_ID.get(model.provider_id) if model else None


def pricing_for(model_id: str) -> Optional[ModelPricing]:
    model = _MODELS_BY_ID.get(model_id)
    return model.pricing if model else None
