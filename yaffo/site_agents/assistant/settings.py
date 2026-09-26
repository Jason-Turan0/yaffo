"""Assistant settings, stored in ApplicationSettings.

- `assistant_enabled`: shows or hides the assistant. On by default.
- `assistant_diag_<group>`: what the assistant may look at (logs, library, files,
  jobs). On by default; all off means knowledge-only. Capture-date metadata is
  a separate, off-by-default group.
- `assistant_action_<host function>`: which library changes a script may propose
  (the HostFunction's `setting_key`). Low- and medium-risk changes are on by
  default; high-risk ones (files on disk) are off until the user turns them on.
- `assistant_confirm_threshold`: above this many items, Approve also asks the user
  to confirm the count. 500 by default.
The model is the one selected under AI Generation, like the page and theme builders.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.automation_host import HostFunction, host_api
from yaffo.db import db
from yaffo.db.models import ApplicationSettings
from yaffo.runtime_mode import reject_in_demo
from yaffo.site_agents import llm_config
from yaffo.site_agents.model_clients import providers

ENABLED_SETTING = "assistant_enabled"

# Diagnostics groups, in the order Settings lists them.
DIAG_LOGS = "logs"
DIAG_LIBRARY = "library"
DIAG_FILES = "files"
DIAG_JOBS = "jobs"
DIAG_METADATA = "metadata"
DIAGNOSTIC_GROUPS = (DIAG_LOGS, DIAG_LIBRARY, DIAG_FILES, DIAG_JOBS, DIAG_METADATA)

CONFIRM_THRESHOLD_SETTING = "assistant_confirm_threshold"
DEFAULT_CONFIRM_THRESHOLD = 500
MAX_CONFIRM_THRESHOLD = 1_000_000


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
    value = _get(session or db.session, _diag_setting(group))
    return value == "true" if group == DIAG_METADATA else value != "false"


def enabled_diagnostics(session: Optional[Session] = None) -> frozenset[str]:
    session = session or db.session
    return frozenset(g for g in DIAGNOSTIC_GROUPS if diagnostics_enabled(g, session))


def set_diagnostics_enabled(group: str, enabled: bool) -> None:
    reject_in_demo("Assistant settings changes")
    _set(_diag_setting(group), "true" if enabled else "false")


def action_functions() -> tuple[HostFunction, ...]:
    """The library changes the assistant may propose: the assistant profile's
    mutating host functions, in HOST_API order."""
    return tuple(fn for fn in host_api("assistant") if fn.mutating)


def _action(name: str) -> HostFunction:
    fn = next((fn for fn in action_functions() if fn.name == name), None)
    if fn is None:
        raise ValueError(f"Unknown assistant action: {name}")
    return fn


def action_enabled(name: str, session: Optional[Session] = None) -> bool:
    fn = _action(name)
    value = _get(session or db.session, fn.setting_key)
    return value == "true" if fn.risk == "high" else value != "false"


def enabled_actions(session: Optional[Session] = None) -> frozenset[str]:
    session = session or db.session
    return frozenset(fn.name for fn in action_functions() if action_enabled(fn.name, session))


def set_action_enabled(name: str, enabled: bool) -> None:
    reject_in_demo("Assistant settings changes")
    _set(_action(name).setting_key, "true" if enabled else "false")


def confirm_threshold(session: Optional[Session] = None) -> int:
    value = _get(session or db.session, CONFIRM_THRESHOLD_SETTING)
    try:
        return max(1, min(int(value), MAX_CONFIRM_THRESHOLD)) if value is not None else DEFAULT_CONFIRM_THRESHOLD
    except ValueError:
        return DEFAULT_CONFIRM_THRESHOLD


def set_confirm_threshold(count: int) -> None:
    reject_in_demo("Assistant settings changes")
    _set(CONFIRM_THRESHOLD_SETTING, str(max(1, min(int(count), MAX_CONFIRM_THRESHOLD))))


def resolve_model(session: Optional[Session] = None) -> str:
    """The model selected under AI Generation; the assistant has no model of its own."""
    return llm_config.get_model(session)


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
