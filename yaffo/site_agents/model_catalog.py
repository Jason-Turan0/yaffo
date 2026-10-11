"""Bounded provider model discovery, with the last successful listing kept locally.

The model APIs run only from Settings. Workers hydrate the same saved catalog,
so new model ids, labels and output limits survive process and app restarts.
"""
from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import requests
from sqlalchemy.orm import Session

from yaffo.db.repositories import llm_model_repository as repository
from yaffo.site_agents.model_clients import providers

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_MODELS = 1000
MAX_PAGES = 5
CATALOG_TTL = timedelta(days=1)
TIMEOUT = (5, 15)


@dataclass(frozen=True)
class RefreshResult:
    provider_id: str
    status: str
    count: int = 0


@dataclass(frozen=True)
class ModelSelectionError:
    error: str

    def to_dict(self) -> dict:
        return asdict(self)


class DiscoveryError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _text_model(provider_id: str, record: dict, api_id: str) -> bool:
    if record.get("lifecycle") == "retired":
        return False
    modalities = record.get("output_modalities")
    if isinstance(modalities, list) and "text" not in modalities:
        return False
    if provider_id == "gemini":
        methods = record.get("supportedGenerationMethods")
        return isinstance(methods, list) and "generateContent" in methods \
            and api_id.startswith("gemini-") and not any(
                word in api_id.lower() for word in ("embedding", "image", "tts", "live", "audio"))
    if provider_id == "openai":
        base = api_id.split(":")[1] if api_id.startswith("ft:") and ":" in api_id else api_id
        family = re.match(r"(?:gpt-(?:4o|4\.1|[5-9])|chatgpt-|o[1-9](?:-|$))", base)
        return bool(family) and not any(word in base.lower() for word in (
            "audio", "realtime", "transcrib", "image", "search", "codex", "-pro"))
    return True


def _model(provider_id: str, record: dict) -> providers.Model | None:
    api_id = record.get("id")
    if provider_id == "gemini" and isinstance(record.get("name"), str):
        api_id = api_id or record["name"].removeprefix("models/")
    if not isinstance(api_id, str) or not api_id or len(api_id) > 200 \
            or any(character.isspace() or ord(character) < 32 for character in api_id) \
            or not _text_model(provider_id, record, api_id):
        return None
    label = record.get("display_name") or record.get("displayName") or record.get("name") or api_id
    label = label[:200] if isinstance(label, str) else api_id
    seeded = next((model for model in providers.MODELS if model.id == api_id and model.provider_id == provider_id), None)
    pricing = seeded.pricing if seeded else None
    if provider_id == "grok":
        rates = [record.get(name) for name in
                 ("prompt_text_token_price", "completion_text_token_price", "cached_prompt_text_token_price")]
        if all(isinstance(rate, (int, float)) and not isinstance(rate, bool)
               and math.isfinite(rate) and rate >= 0 for rate in rates):
            pricing = providers.ModelPricing(*(rate / 10_000 for rate in rates))
    limit = _positive_int(record.get("max_output_tokens") or record.get("outputTokenLimit") or record.get("max_tokens"))
    if provider_id == "openai" and limit is None:
        if api_id.startswith(("gpt-4o", "chatgpt-4o")):
            limit = 16_384
        elif api_id.startswith("gpt-4.1") or api_id == "o1-preview":
            limit = 32_768
    return providers.Model(api_id, label, provider_id, pricing, limit)


def discover(provider: providers.Provider, api_key: str) -> list[providers.Model]:
    endpoints = {
        "anthropic": "https://api.anthropic.com/v1/models",
        "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
        "grok": "https://api.x.ai/v1/language-models",
    }
    endpoint = endpoints.get(provider.id) or f"{provider.base_url.rstrip('/')}/models"
    headers = {"Accept": "application/json"}
    params = {}
    if provider.id == "anthropic":
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
        params["limit"] = 100
    elif provider.id == "gemini":
        headers["x-goog-api-key"] = api_key
        params["pageSize"] = 100
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    found = {}
    cursors: set[str] = set()
    with requests.Session() as http:
        http.trust_env = False
        for _ in range(MAX_PAGES):
            try:
                with http.get(endpoint, headers=headers, params=params, timeout=TIMEOUT,
                              allow_redirects=False, stream=True) as response:
                    if response.status_code in (401, 403):
                        raise DiscoveryError("auth")
                    if response.status_code != 200:
                        raise DiscoveryError("unavailable")
                    raw = bytearray()
                    for chunk in response.iter_content(64 * 1024):
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise DiscoveryError("too_large")
                    payload = json.loads(raw)
            except requests.RequestException:
                raise DiscoveryError("unavailable") from None
            except (ValueError, TypeError):
                raise DiscoveryError("invalid_response") from None
            if not isinstance(payload, dict):
                raise DiscoveryError("invalid_response")
            records = payload.get("models" if provider.id in {"gemini", "grok"} else "data")
            if not isinstance(records, list) or len(records) > MAX_MODELS:
                raise DiscoveryError("invalid_response")
            for record in records:
                if isinstance(record, dict) and (model := _model(provider.id, record)) is not None:
                    found[model.id] = model
            if len(found) > MAX_MODELS:
                raise DiscoveryError("too_large")
            cursor = (payload.get("last_id") if provider.id == "anthropic" and payload.get("has_more") else
                      payload.get("nextPageToken") if provider.id == "gemini" else None)
            if not cursor:
                if not found:
                    raise DiscoveryError("no_models")
                return sorted(found.values(), key=lambda model: model.label.casefold())
            if not isinstance(cursor, str) or len(cursor) > 2048 or cursor in cursors:
                raise DiscoveryError("invalid_response")
            cursors.add(cursor)
            params["after_id" if provider.id == "anthropic" else "pageToken"] = cursor
    raise DiscoveryError("too_large")


def cached_models(session: Session) -> list[providers.Model]:
    saved = repository.catalogs(session)
    found = []
    for provider in providers.PROVIDERS:
        records = saved.get(provider.id, {}).get("models")
        models = []
        if isinstance(records, list):
            for record in records[:MAX_MODELS]:
                if not isinstance(record, dict):
                    continue
                api_id, label = record.get("id"), record.get("label")
                if not isinstance(api_id, str) or not api_id or len(api_id) > 200 \
                        or any(character.isspace() or ord(character) < 32 for character in api_id) \
                        or not isinstance(label, str):
                    continue
                pricing = None
                raw_pricing = record.get("pricing")
                if isinstance(raw_pricing, dict):
                    rates = [raw_pricing.get(key) for key in ("input", "output", "cached_input")]
                    if all(isinstance(rate, (int, float)) and not isinstance(rate, bool)
                           and math.isfinite(rate) and rate >= 0 for rate in rates):
                        pricing = providers.ModelPricing(*rates)
                models.append(providers.Model(api_id, label[:200], provider.id, pricing,
                                              _positive_int(record.get("max_output_tokens"))))
        found.extend(models or [model for model in providers.MODELS if model.provider_id == provider.id])
    providers.register_discovered_models(found)
    return found


def refresh(session: Session, get_api_key: Callable[[str], str | None], *, force: bool = False,
            provider_id: str | None = None) -> list[RefreshResult]:
    saved = repository.catalogs(session)
    now = datetime.now(timezone.utc)
    pending = []
    results = []
    for provider in providers.PROVIDERS:
        if provider_id is not None and provider.id != provider_id:
            continue
        key = get_api_key(provider.id)
        if not key:
            continue
        try:
            stamp = datetime.fromisoformat(saved.get(provider.id, {}).get("refreshed_at") or "")
            fresh = timedelta() <= now - stamp < CATALOG_TTL
        except (ValueError, TypeError):
            fresh = False
        if fresh and not force:
            continue
        pending.append((provider, key))

    session.commit()
    keys = {provider.id: key for provider, key in pending}

    def fetch(pair: tuple[providers.Provider, str]) -> tuple[str, list[providers.Model] | str]:
        provider, key = pair
        try:
            return provider.id, discover(provider, key)
        except DiscoveryError as error:
            return provider.id, error.code
    with ThreadPoolExecutor(max_workers=3) as executor:
        for provider_id, outcome in executor.map(fetch, pending):
            if isinstance(outcome, str):
                results.append(RefreshResult(provider_id, outcome))
            else:
                if get_api_key(provider_id) != keys[provider_id]:
                    results.append(RefreshResult(provider_id, "key_changed"))
                    continue
                repository.save(session, provider_id, {"refreshed_at": now.isoformat(),
                                                       "models": [asdict(model) for model in outcome]})
                results.append(RefreshResult(provider_id, "ok", len(outcome)))
    session.commit()
    return results
