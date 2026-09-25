"""Assistant settings, stored in ApplicationSettings.

- `assistant_enabled`: shows or hides the assistant. On by default.
The model is automatically the cheapest model of the AI Generation provider.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import ApplicationSettings
from yaffo.runtime_mode import reject_in_demo
from yaffo.site_agents import llm_config
from yaffo.site_agents.model_clients import providers

ENABLED_SETTING = "assistant_enabled"


def _get(session: Session, name: str) -> Optional[str]:
    row = session.query(ApplicationSettings).filter_by(name=name).first()
    return row.value if row is not None else None


def _set(name: str, value: str) -> None:
    row = db.session.query(ApplicationSettings).filter_by(name=name).first()
    if row is None:
        db.session.add(ApplicationSettings(name=name, type="string", value=value))
    else:
        row.value = value
    db.session.commit()


def is_enabled(session: Optional[Session] = None) -> bool:
    return _get(session or db.session, ENABLED_SETTING) != "false"


def set_enabled(enabled: bool) -> None:
    reject_in_demo("Assistant settings changes")
    _set(ENABLED_SETTING, "true" if enabled else "false")


def default_model(session: Optional[Session] = None) -> str:
    """The cheapest model (by output price) of the AI Generation provider."""
    provider_id = llm_config.selected_model_provider(session)
    candidates = [m for m in providers.models() if m.provider_id == provider_id]
    if not candidates:
        return llm_config.get_model(session)
    return min(candidates, key=lambda m: (m.pricing.output, m.pricing.input)).id


def resolve_model(session: Optional[Session] = None) -> str:
    return default_model(session)


def model_provider_id(session: Optional[Session] = None) -> str:
    provider = providers.provider_for_model(resolve_model(session))
    return provider.id if provider else llm_config.selected_model_provider(session)


def api_key(session: Optional[Session] = None) -> Optional[str]:
    return llm_config.get_api_key(model_provider_id(session))


def provider_label(session: Optional[Session] = None) -> str:
    provider = providers.get_provider(model_provider_id(session))
    return provider.label if provider else llm_config.selected_provider_label(session)
