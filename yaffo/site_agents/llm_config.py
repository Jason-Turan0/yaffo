"""LLM configuration for the page builder: per-provider API keys (secrets) + the
selected model.

- Each vendor's API key lives in the OS keychain via keyring (its env var wins).
  Never stored in the database or a file. Providers + their env-var/keychain names
  come from the model_clients.providers registry.
- Selected model is non-sensitive config, stored in `ApplicationSettings` alongside
  the app's other settings; the registry maps it to its provider.

See docs/development/ai-page-builder.md.
"""
from __future__ import annotations

import os
from typing import Optional

import keyring
from flask_babel import gettext
from cachetools import TTLCache, cached
from keyring.errors import KeyringError, PasswordDeleteError

from yaffo.db import db
from yaffo.db.models import ApplicationSettings
from yaffo.site_agents.model_clients import providers
from yaffo.site_agents import model_catalog
from yaffo.db.repositories import llm_model_repository
from yaffo.runtime_mode import demo_mode_enabled, reject_in_demo

_SERVICE = "yaffo"
_MODEL_SETTING = "llm_model"

DEFAULT_MODEL = "claude-sonnet-4-6"


# ---- API keys (keychain, per provider) ------------------------------------

# Reading the keychain pops an OS permission prompt on macOS, so memoize per provider.
# ttl=300 (5 min) so a key configured/rotated after this process started is picked up
# without a restart. set/clear invalidate the cache so the change is visible immediately
# in the process that made it. In-memory only — the secret never touches disk.
_key_cache: TTLCache = TTLCache(maxsize=len(providers.PROVIDERS) + 1, ttl=300)


@cached(cache=_key_cache)
def _read_keychain_key(keychain_key: str) -> Optional[str]:
    try:
        return keyring.get_password(_SERVICE, keychain_key)
    except KeyringError:
        return None


def get_api_key(provider_id: str) -> Optional[str]:
    """The key for one provider. Env var wins (also how spawned workers inherit it —
    see prime_subprocess_env); then the cached keychain value."""
    if demo_mode_enabled():
        return None
    provider = providers.get_provider(provider_id)
    if provider is None:
        return None
    env = os.environ.get(provider.env_var)
    if env:
        return env
    return _read_keychain_key(provider.keychain_key)


def set_api_key(provider_id: str, key: str) -> None:
    reject_in_demo("LLM key changes")
    provider = providers.get_provider(provider_id)
    if provider is None:
        return
    keyring.set_password(_SERVICE, provider.keychain_key, key)
    _key_cache.clear()
    llm_model_repository.invalidate(db.session, provider_id)
    db.session.commit()


def clear_api_key(provider_id: str) -> None:
    reject_in_demo("LLM key changes")
    provider = providers.get_provider(provider_id)
    if provider is None:
        return
    try:
        keyring.delete_password(_SERVICE, provider.keychain_key)
    except PasswordDeleteError:
        pass
    _key_cache.clear()
    llm_model_repository.invalidate(db.session, provider_id)
    db.session.commit()


def prime_subprocess_env() -> None:
    """Publish every configured provider key into this process's environment so
    spawn-started children inherit them via the providers' env vars and never touch
    the keychain themselves. Called by the task host before spawning workers — keychain
    prompts then happen only in the interactive web process and (once) the host."""
    reject_in_demo("LLM subprocess credentials")
    for provider in providers.PROVIDERS:
        key = get_api_key(provider.id)
        if key:
            os.environ[provider.env_var] = key


def _api_key_status(provider) -> dict:
    if os.environ.get(provider.env_var):
        return {"configured": True, "source": "environment"}
    stored = get_api_key(provider.id)
    return {"configured": bool(stored), "source": "keychain" if stored else None}


def provider_status(provider_id: str) -> Optional[dict]:
    """Key-status dict for one provider, for the single key box in the Settings UI.
    None for an unknown provider id."""
    provider = providers.get_provider(provider_id)
    if provider is None:
        return None
    return {"id": provider.id, "label": provider.label, "env_var": provider.env_var,
            **_api_key_status(provider)}


# ---- Model (ApplicationSettings) ------------------------------------------

def get_model(session=None) -> str:
    """The selected model id. `session` is injected by the background worker (which
    has no Flask app context, so the request-scoped db.session is unavailable);
    request-path callers omit it and get db.session."""
    session = session or db.session
    model_catalog.cached_models(session)
    row = session.query(ApplicationSettings).filter_by(name=_MODEL_SETTING).first()
    if row and providers.get_model(row.value) is not None:
        return row.value
    return DEFAULT_MODEL


def set_model(model_id: str) -> bool:
    reject_in_demo("LLM model changes")
    available = {providers.selection_id(model) for model in model_catalog.cached_models(db.session)}
    if model_id not in available and model_id != get_model():
        return False
    row = db.session.query(ApplicationSettings).filter_by(name=_MODEL_SETTING).first()
    if row is None:
        db.session.add(ApplicationSettings(name=_MODEL_SETTING, type="string", value=model_id))
    else:
        row.value = model_id
    db.session.commit()
    return True


def selected_model_provider(session=None) -> str:
    """The provider id backing the currently-selected model."""
    provider = providers.provider_for_model(get_model(session))
    return provider.id if provider else "anthropic"


def get_api_key_for_selected_model(session=None) -> Optional[str]:
    return get_api_key(selected_model_provider(session))


def selected_provider_label(session=None) -> str:
    """Human label of the selected model's provider, for 'configure your <vendor> key'
    messages."""
    provider = providers.get_provider(selected_model_provider(session))
    return provider.label if provider else "the selected provider"


def selected_key_missing(session=None) -> bool:
    """Whether the selected model's provider has no API key — the gate the generation
    routes use before kicking off an agent run."""
    return get_api_key_for_selected_model(session) is None


# ---- Combined status for the UI -------------------------------------------

def status() -> dict:
    """Presence-only status for the Settings UI. Never returns a key itself. Includes
    the selected model, the full model list (for the grouped dropdown), the providers
    list (for the optgroup labels), and the key status of the *selected* model's
    provider (the single key box shown)."""
    models = model_catalog.cached_models(db.session)
    selected = get_model()
    if selected not in {providers.selection_id(model) for model in models}:
        models.append(providers.get_model(selected))
    selected_provider = selected_model_provider()
    return {
        "model": selected,
        "models": [{"id": providers.selection_id(model), "label": model.label, "provider": model.provider_id}
                   for model in models],
        "selected_provider": selected_provider,
        "selected_provider_status": provider_status(selected_provider),
        "providers": [{"id": p.id, "label": p.label} for p in providers.PROVIDERS],
    }


def localized_status() -> dict:
    """status() with the model labels that have translations localized, for the
    AI Generation and Assistant model dropdowns. Needs a request/app context."""
    result = status()
    model_labels = {
        "Claude Opus 4.8 — most capable": gettext("Claude Opus 4.8 — most capable"),
        "Claude Sonnet 4.6 — balanced": gettext("Claude Sonnet 4.6 — balanced"),
        "Claude Haiku 4.5 — fastest": gettext("Claude Haiku 4.5 — fastest"),
    }
    result["models"] = [
        {**model, "label": model_labels.get(model["label"], model["label"])}
        for model in result["models"]
    ]
    active = next(model for model in result["models"] if model["id"] == result["model"])
    provider = next(provider for provider in result["providers"] if provider["id"] == active["provider"])
    result["active_model_label"] = f"{provider['label']} · {active['label']}"
    return result
