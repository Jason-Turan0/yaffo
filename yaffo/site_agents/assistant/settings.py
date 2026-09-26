"""Assistant settings, stored in ApplicationSettings.

- `assistant_enabled`: shows or hides the assistant. On by default.
- `assistant_diag_<group>`: what the assistant may look at (logs, library, files,
  jobs). On by default; all off means knowledge-only.
- `assistant_redact_people`: replace people names with `Person #<id>` in what is
  sent to the model. Off by default.
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
REDACT_PEOPLE_SETTING = "assistant_redact_people"

# Diagnostics groups, in the order Settings lists them.
DIAG_LOGS = "logs"
DIAG_LIBRARY = "library"
DIAG_FILES = "files"
DIAG_JOBS = "jobs"
DIAGNOSTIC_GROUPS = (DIAG_LOGS, DIAG_LIBRARY, DIAG_FILES, DIAG_JOBS)


def _diag_setting(group: str) -> str:
    if group not in DIAGNOSTIC_GROUPS:
        raise ValueError(f"Unknown diagnostics group: {group}")
    return f"assistant_diag_{group}"


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


def diagnostics_enabled(group: str, session: Optional[Session] = None) -> bool:
    return _get(session or db.session, _diag_setting(group)) != "false"


def enabled_diagnostics(session: Optional[Session] = None) -> frozenset[str]:
    session = session or db.session
    return frozenset(g for g in DIAGNOSTIC_GROUPS if diagnostics_enabled(g, session))


def set_diagnostics_enabled(group: str, enabled: bool) -> None:
    reject_in_demo("Assistant settings changes")
    _set(_diag_setting(group), "true" if enabled else "false")


def redact_people(session: Optional[Session] = None) -> bool:
    return _get(session or db.session, REDACT_PEOPLE_SETTING) == "true"


def set_redact_people(enabled: bool) -> None:
    reject_in_demo("Assistant settings changes")
    _set(REDACT_PEOPLE_SETTING, "true" if enabled else "false")


def default_model(session: Optional[Session] = None) -> str:
    """The cheapest model (by output price) of the AI Generation provider."""
    provider_id = llm_config.selected_model_provider(session)
    candidates = [m for m in providers.models() if m.provider_id == provider_id]
    if not candidates:
        return llm_config.get_model(session)
    return min(candidates, key=lambda m: (m.pricing.output, m.pricing.input)).id


def resolve_model(session: Optional[Session] = None) -> str:
    return default_model(session)


def model_label(session: Optional[Session] = None) -> str:
    """The assistant model's display name (e.g. "Claude Haiku 4.5")."""
    model_id = resolve_model(session)
    model = providers.get_model(model_id)
    return model.label if model else model_id


def model_provider_id(session: Optional[Session] = None) -> str:
    provider = providers.provider_for_model(resolve_model(session))
    return provider.id if provider else llm_config.selected_model_provider(session)


def api_key(session: Optional[Session] = None) -> Optional[str]:
    return llm_config.get_api_key(model_provider_id(session))


def provider_label(session: Optional[Session] = None) -> str:
    provider = providers.get_provider(model_provider_id(session))
    return provider.label if provider else llm_config.selected_provider_label(session)
