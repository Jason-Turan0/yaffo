"""LLM configuration for the page builder: API key (secret) + selected model.

- API key lives in the OS keychain via keyring (env var `ANTHROPIC_API_KEY`
  wins). Never stored in the database or a file.
- Selected model is non-sensitive config, stored in `ApplicationSettings`
  alongside the app's other settings.

See docs/ai-page-builder.md.
"""
from __future__ import annotations

import os
from typing import Optional

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from yaffo.db import db
from yaffo.db.models import ApplicationSettings

_SERVICE = "yaffo"
_KEY_NAME = "anthropic_api_key"
_ENV_VAR = "ANTHROPIC_API_KEY"
_MODEL_SETTING = "llm_model"

AVAILABLE_MODELS = [
    {"id": "claude-opus-4-8", "label": "Claude Opus 4.8 — most capable"},
    {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6 — balanced"},
    {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5 — fastest"},
]
DEFAULT_MODEL = "claude-sonnet-4-6"
_MODEL_IDS = {m["id"] for m in AVAILABLE_MODELS}


# ---- API key (keychain) ---------------------------------------------------

def get_api_key() -> Optional[str]:
    """The only place that reads the key. Env var wins; then the keychain."""
    env = os.environ.get(_ENV_VAR)
    if env:
        return env
    try:
        return keyring.get_password(_SERVICE, _KEY_NAME)
    except KeyringError:
        return None


def set_api_key(key: str) -> None:
    keyring.set_password(_SERVICE, _KEY_NAME, key)


def clear_api_key() -> None:
    try:
        keyring.delete_password(_SERVICE, _KEY_NAME)
    except PasswordDeleteError:
        pass


def _api_key_status() -> dict:
    if os.environ.get(_ENV_VAR):
        return {"configured": True, "source": "environment"}
    try:
        stored = keyring.get_password(_SERVICE, _KEY_NAME)
    except KeyringError:
        stored = None
    return {"configured": bool(stored), "source": "keychain" if stored else None}


# ---- Model (ApplicationSettings) ------------------------------------------

def get_model(session=None) -> str:
    """The selected model id. `session` is injected by the background worker (which
    has no Flask app context, so the request-scoped db.session is unavailable);
    request-path callers omit it and get db.session."""
    session = session or db.session
    row = session.query(ApplicationSettings).filter_by(name=_MODEL_SETTING).first()
    if row and row.value in _MODEL_IDS:
        return row.value
    return DEFAULT_MODEL


def set_model(model_id: str) -> None:
    if model_id not in _MODEL_IDS:
        return
    row = db.session.query(ApplicationSettings).filter_by(name=_MODEL_SETTING).first()
    if row is None:
        db.session.add(ApplicationSettings(name=_MODEL_SETTING, type="string", value=model_id))
    else:
        row.value = model_id
    db.session.commit()


# ---- Combined status for the UI -------------------------------------------

def status() -> dict:
    """Presence-only status for the Settings UI. Never returns the key itself."""
    return {**_api_key_status(), "model": get_model(), "models": AVAILABLE_MODELS}