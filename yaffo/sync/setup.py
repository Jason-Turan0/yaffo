"""Creating and configuring sync automations: what the setup page (and tests) call.

A new sync starts disabled with `needs_consent`, its options at their defaults, an
existing account for its provider preselected (connections are shared), and the
default triggers:
- sources: a nightly schedule;
- targets: a nightly schedule, `media_deleted` filtered to this sync, and, for an
  album selection, `album_changed` filtered to the album. The event triggers carry
  a settle window, so a burst of changes is one run.
Every change to what the consent covers clears the consent (the sync turns off
until the user turns it on again).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from yaffo.db.models import (
    Automation, AutomationTrigger, SyncAdapter,
    AUTOMATION_STATUS_ACCEPTED, EVENT_ALBUM_CHANGED, EVENT_MEDIA_DELETED, SYNC_ROLE_SOURCE, SYNC_ROLE_TARGET,
    TRIGGER_TYPE_EVENT, TRIGGER_TYPE_SCHEDULE,
)
from yaffo.db.repositories import automation_repository, sync_repository
from yaffo.sync import access, connections
from yaffo.sync.adapters import LoadedAdapter, load_adapter
from yaffo.sync.consent import consent_hash
from yaffo.sync.options import option_defaults, validate_options
from yaffo.sync.schemas import (
    DEFAULT_IMPORT_LAYOUT, IMPORT_LAYOUTS, RUNTIME_NEEDS_CONSENT, RUNTIME_READY, SELECTION_ALBUM, Selection,
    SyncConfig, SyncRuntime,
)

NIGHTLY_CRON = "0 2 * * *"
SETTLE_SECONDS = 60
DEFAULT_IMPORT_SUBPATH = "Imports"

TRIGGER_KEY_ALBUM = "album_id"
TRIGGER_KEY_SYNC = "sync_automation_id"
TRIGGER_KEY_SETTLE = "settle_seconds"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_import_subpath(name: str) -> str:
    return f"{DEFAULT_IMPORT_SUBPATH}/{name.strip() or 'Sync'}"


def create_sync(session: Session, name: str, adapter_id: int) -> Automation:
    adapter = load_adapter(session, adapter_id)
    existing = connections.find_for_provider(session, adapter.provider) if adapter.needs_connection else []
    config = SyncConfig(role=adapter.role, adapter_id=adapter.id,
                        connection_key=existing[0][0] if existing else None, scopes_needed=adapter.scopes,
                        options=option_defaults(adapter.options), import_root=None, selection=None,
                        consent_hash=None, consented_at=None)
    automation = Automation(slug=automation_repository.unique_slug(session, name), name=name, is_system=False,
                            enabled=False, status=AUTOMATION_STATUS_ACCEPTED)
    automation.sync = {"config": config.to_dict(), "runtime": SyncRuntime(status=RUNTIME_NEEDS_CONSENT).to_dict()}
    session.add(automation)
    session.flush()
    session.add(AutomationTrigger(automation_id=automation.id, trigger_type=TRIGGER_TYPE_SCHEDULE,
                                  cron=NIGHTLY_CRON, enabled=True))
    if adapter.role == SYNC_ROLE_TARGET:
        session.add(AutomationTrigger(
            automation_id=automation.id, trigger_type=TRIGGER_TYPE_EVENT, event_type=EVENT_MEDIA_DELETED,
            enabled=True, config={TRIGGER_KEY_SYNC: automation.id, TRIGGER_KEY_SETTLE: SETTLE_SECONDS}))
    session.commit()
    return automation


def _config(automation: Automation) -> SyncConfig:
    return sync_repository.load_sync(automation).config


def _store(session: Session, automation: Automation, config: SyncConfig) -> None:
    """Save a config change. Anything the consent covers turns the sync off until
    it's approved again."""
    adapter = load_adapter(session, config.adapter_id)
    changed = config.consent_hash is not None and config.consent_hash != consent_hash(config, adapter)
    if changed:
        config = replace(config, consent_hash=None, consented_at=None)
    sync_repository.save_config(automation, config)
    if config.consent_hash is None:
        automation.enabled = False
        runtime = sync_repository.load_sync(automation).runtime
        sync_repository.save_runtime(automation, replace(runtime, status=RUNTIME_NEEDS_CONSENT, pause_code=None))
    session.commit()


def set_options(session: Session, automation: Automation, values: dict) -> None:
    """Validate and store the adapter options. Raises OptionError."""
    config = _config(automation)
    adapter = load_adapter(session, config.adapter_id)
    _store(session, automation, replace(config, options=validate_options(adapter.options, values)))


def set_connection(session: Session, automation: Automation, connection_key: str) -> None:
    """Use this account for the sync. Switching accounts re-asks consent, clears the
    remote options (folder ids belong to an account), and releases the old
    connection if nothing else uses it."""
    config = _config(automation)
    adapter = load_adapter(session, config.adapter_id)
    connection = connections.get(session, connection_key)
    if connection is None or connection.provider != adapter.provider:
        raise ValueError("that account isn't for this service")
    previous = config.connection_key
    options = config.options
    if previous and previous != connection_key:
        options = {key: (None if spec["type"].startswith("remote_") else options.get(key))
                   for key, spec in adapter.options.items()}
    _store(session, automation, replace(config, connection_key=connection_key, scopes_needed=adapter.scopes,
                                        options=options))
    if previous and previous != connection_key:
        connections.release_if_unreferenced(session, previous)


def set_import_root(session: Session, automation: Automation, media_dir_id: str, subpath: str,
                    layout: str = DEFAULT_IMPORT_LAYOUT) -> None:
    """Where a source's new files go: a subfolder of a media folder, arranged by
    `layout`. Changing any of it re-asks consent. Raises ValueError for an unknown
    layout."""
    if layout not in IMPORT_LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}")
    config = _config(automation)
    cleaned = "/".join(part for part in subpath.replace("\\", "/").split("/") if part.strip())
    _store(session, automation, replace(config, import_root={"media_dir_id": media_dir_id, "subpath": cleaned,
                                                             "layout": layout}))


def _album_trigger(automation: Automation) -> AutomationTrigger | None:
    return next((t for t in automation.triggers
                 if t.trigger_type == TRIGGER_TYPE_EVENT and t.event_type == EVENT_ALBUM_CHANGED
                 and (t.config or {}).get(TRIGGER_KEY_SYNC) == automation.id), None)


def set_selection(session: Session, automation: Automation, selection: Selection) -> None:
    """Store the target's selection and keep its owned album_changed trigger in step
    (created, re-pointed, or removed in the same transaction)."""
    config = _config(automation)
    trigger = _album_trigger(automation)
    if selection.kind == SELECTION_ALBUM:
        trigger_config = {TRIGGER_KEY_ALBUM: selection.album_id, TRIGGER_KEY_SYNC: automation.id,
                          TRIGGER_KEY_SETTLE: SETTLE_SECONDS}
        if trigger is None:
            session.add(AutomationTrigger(automation_id=automation.id, trigger_type=TRIGGER_TYPE_EVENT,
                                          event_type=EVENT_ALBUM_CHANGED, enabled=True, config=trigger_config))
        else:
            trigger.config = trigger_config
    elif trigger is not None:
        session.delete(trigger)
    _store(session, automation, replace(config, selection=selection))


def missing_setup(session: Session, automation: Automation) -> list[str]:
    """What still has to be filled in before the sync can be turned on."""
    config = _config(automation)
    adapter = load_adapter(session, config.adapter_id)
    missing = [f"option:{key}" for key, spec in adapter.options.items()
               if spec.get("required") and not config.options.get(key)]
    if config.role == SYNC_ROLE_SOURCE and not config.import_root:
        missing.append("import_root")
    if config.role == SYNC_ROLE_TARGET and config.selection is None:
        missing.append("selection")
    if adapter.needs_connection and access.account_problem(session, adapter, config):
        missing.append("account")
    return missing


def turn_on(session: Session, automation: Automation) -> None:
    """Record the user's consent to the current setup and enable the sync."""
    config = _config(automation)
    adapter = load_adapter(session, config.adapter_id)
    if missing_setup(session, automation):
        raise ValueError("the setup isn't complete")
    config = replace(config, consent_hash=consent_hash(config, adapter), consented_at=_now_iso())
    sync_repository.save_config(automation, config)
    runtime = sync_repository.load_sync(automation).runtime
    sync_repository.save_runtime(automation, replace(runtime, status=RUNTIME_READY, pause_code=None))
    automation.enabled = True
    session.commit()


def turn_off(session: Session, automation: Automation) -> None:
    automation.enabled = False
    session.commit()


def builtin_adapter_row(session: Session, handler: str) -> SyncAdapter | None:
    return session.query(SyncAdapter).filter_by(handler=handler).first()


def adapter_of(session: Session, automation: Automation) -> LoadedAdapter:
    return load_adapter(session, _config(automation).adapter_id)
