"""Connections: signed-in accounts, one `application_settings` row per account.

The row is named `sync_connection:<provider>:<account_id>` (the unique name is what
makes "one connection per account" hold) and holds a SyncConnection as JSON. The
token itself is in the keychain (secrets_store), named by `secret_ref`. Every sync
and adapter that uses the account refers to the row by that name, so one sign-in
serves them all, and one "Sign in again" fixes them all.

Writes that can happen while a sync run is mid-page (the refresh lease, marking an
account `needs_sign_in`) use their own short session on the same database, so they
never commit the run's half-finished work. Callers only take the lease at a page
boundary, when the run holds no write lock. See
docs/development/automation-sync-adapters.md (Connection).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from yaffo.db.models import ApplicationSettings, SyncAdapter
from yaffo.logging_config import get_logger
from yaffo.sync import secrets_store
from yaffo.sync.schemas import (
    CONNECTION_NEEDS_SIGN_IN, CONNECTION_READY, SyncConnection, SyncSchemaError,
)

logger = get_logger(__name__)

PREFIX = "sync_connection:"
SETTING_TYPE = "json"
LEASE_SECONDS = 60
LEASE_WAIT_SECONDS = 15
_SAFE_ID = re.compile(r"^[A-Za-z0-9._@+-]{1,128}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def safe_account_id(account_id: str) -> str:
    """The account id as it appears in the row name and the keychain entry: as is
    when it's plain, a hash otherwise."""
    if _SAFE_ID.match(account_id):
        return account_id
    return "h" + hashlib.sha256(account_id.encode("utf-8")).hexdigest()[:40]


def connection_key(provider: str, account_id: str) -> str:
    return f"{PREFIX}{provider}:{safe_account_id(account_id)}"


def secret_ref(provider: str, account_id: str) -> str:
    return f"sync.{provider}.{safe_account_id(account_id)}"


def _row(session: Session, key: str) -> ApplicationSettings | None:
    if not key.startswith(PREFIX):
        return None
    return session.query(ApplicationSettings).filter(ApplicationSettings.name == key).first()


def _decode(row: ApplicationSettings) -> SyncConnection | None:
    try:
        return SyncConnection.from_dict(json.loads(row.value or "null"))
    except (ValueError, SyncSchemaError) as e:
        logger.warning(f"connection {row.name} is unreadable: {e}")
        return None


def _encode(connection: SyncConnection) -> str:
    return json.dumps(connection.to_dict(), sort_keys=True)


def get(session: Session, key: str | None) -> SyncConnection | None:
    if not key:
        return None
    row = _row(session, key)
    return _decode(row) if row is not None else None


def find_for_provider(session: Session, provider: str) -> list[tuple[str, SyncConnection]]:
    rows = (session.query(ApplicationSettings)
            .filter(ApplicationSettings.name.like(f"{PREFIX}{provider}:%"))
            .order_by(ApplicationSettings.id).all())
    found = []
    for row in rows:
        connection = _decode(row)
        if connection is not None and connection.provider == provider:
            found.append((row.name, connection))
    return found


def upsert_after_sign_in(session: Session, provider: str, account_id: str, account_label: str | None,
                         scopes: list[str], secret: dict, instance: dict | None = None) -> str:
    """Store a fresh sign-in: the token (or key) in the keychain, the row ready.
    Signing in to an account that already has a row updates it in place, so every
    sync using it is fixed at once. `instance` is a self-hosted server's address.
    Commits."""
    key = connection_key(provider, account_id)
    ref = secret_ref(provider, account_id)
    secrets_store.store_secret(ref, secret)
    connection = SyncConnection(provider=provider, account_id=account_id, account_label=account_label,
                                scopes=sorted(set(scopes)), status=CONNECTION_READY, secret_ref=ref,
                                instance=dict(instance or {}), refreshing_until=None)
    row = _row(session, key)
    if row is None:
        session.add(ApplicationSettings(name=key, type=SETTING_TYPE, value=_encode(connection)))
    else:
        row.type = SETTING_TYPE
        row.value = _encode(connection)
    session.commit()
    return key


def _own_session(bind: Engine | Connection) -> Session:
    return Session(bind=bind, expire_on_commit=False)


def mark_needs_sign_in(bind: Engine | Connection, key: str) -> None:
    """The provider refused the account's credentials: every sync using it shows
    "Sign in again". Uses its own session (see the module docs)."""
    with _own_session(bind) as session:
        row = _row(session, key)
        connection = _decode(row) if row is not None else None
        if connection is None or connection.status == CONNECTION_NEEDS_SIGN_IN:
            return
        row.value = _encode(replace(connection, status=CONNECTION_NEEDS_SIGN_IN, refreshing_until=None))
        session.commit()


def take_refresh_lease(bind: Engine | Connection, key: str) -> bool:
    """Try to become the one process refreshing this account's token: a conditional
    UPDATE that only succeeds if nobody holds an unexpired lease. Returns False
    when someone else holds it (the caller waits and re-reads the keychain)."""
    with _own_session(bind) as session:
        row = _row(session, key)
        connection = _decode(row) if row is not None else None
        if connection is None:
            return False
        until = _parse(connection.refreshing_until)
        if until is not None and until > _now():
            return False
        old_value = row.value
        new_value = _encode(replace(connection, refreshing_until=_iso(_now() + timedelta(seconds=LEASE_SECONDS))))
        result = session.execute(
            update(ApplicationSettings)
            .where(ApplicationSettings.name == key, ApplicationSettings.value == old_value)
            .values(value=new_value)
        )
        session.commit()
        return bool(result.rowcount)


def release_refresh_lease(bind: Engine | Connection, key: str) -> None:
    with _own_session(bind) as session:
        row = _row(session, key)
        connection = _decode(row) if row is not None else None
        if connection is None or connection.refreshing_until is None:
            return
        row.value = _encode(replace(connection, refreshing_until=None))
        session.commit()


def wait_for_lease(bind: Engine | Connection, key: str, timeout: float = LEASE_WAIT_SECONDS,
                   poll: float = 0.5) -> None:
    """Wait (briefly) for another process's refresh to finish."""
    stop = time.monotonic() + timeout
    while time.monotonic() < stop:
        with _own_session(bind) as session:
            connection = get(session, key)
        until = _parse(connection.refreshing_until) if connection else None
        if until is None or until <= _now():
            return
        time.sleep(poll)


def references(session: Session, key: str) -> list[int]:
    """Ids of the sync automations using this connection."""
    from yaffo.db.repositories import sync_repository

    found = []
    for automation in sync_repository.sync_automations(session):
        try:
            config = sync_repository.load_sync(automation).config
        except SyncSchemaError:
            continue
        if config.connection_key == key:
            found.append(automation.id)
    return found


def release_if_unreferenced(session: Session, key: str | None) -> bool:
    """Delete a connection (row and keychain entry) that no sync and no adapter
    refers to any more. Returns whether it was deleted. Commits."""
    if not key:
        return False
    row = _row(session, key)
    if row is None:
        return False
    if references(session, key):
        return False
    if session.query(SyncAdapter.id).filter(SyncAdapter.test_connection_key == key).first():
        return False
    connection = _decode(row)
    session.delete(row)
    session.commit()
    if connection is not None:
        secrets_store.delete_secret(connection.secret_ref)
    logger.info(f"released connection {key}: nothing uses it any more")
    return True
