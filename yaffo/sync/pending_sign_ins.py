"""Sign-ins in progress, kept so a restart of the web app doesn't lose them.

A browser sign-in waits for the provider to send the browser back; a device-code
sign-in waits for the user to enter the code on another device. Either can take
minutes, and the web app may restart in between (an update, a crash, the dev
reloader). Each pending sign-in is one `application_settings` row named
`sync_pending_sign_in:<id>` with what isn't secret (provider, scopes, where to return,
the code shown to the user, when to poll next, when it expires). The secret half (the
PKCE verifier, or the device code that redeems the sign-in) is in the keychain, like
every other credential (secrets_store), and is removed with the row.

The id is the OAuth `state` (browser) or the polling handle (device code): random
and single-use. `claim` takes a pending sign-in exactly once, so a repeated return
address or a double poll can't redeem it twice. Expired entries are purged whenever
a new sign-in starts.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

from sqlalchemy import delete
from sqlalchemy.orm import Session

from yaffo.db.models import ApplicationSettings
from yaffo.sync import secrets_store

PREFIX = "sync_pending_sign_in:"
SETTING_TYPE = "json"
KIND_BROWSER = "browser"
KIND_DEVICE = "device"


@dataclass
class PendingSignIn:
    id: str
    kind: str
    provider: str
    scopes: list[str]
    automation_slug: str | None
    return_to: str
    expires_at: float
    # browser sign-ins
    redirect_uri: str | None = None
    # device-code sign-ins
    user_code: str | None = None
    verification_uri: str | None = None
    interval: int = 5
    next_poll: float = 0.0

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at


def _name(pending_id: str) -> str:
    return f"{PREFIX}{pending_id}"


def _secret_ref(pending_id: str) -> str:
    return f"sync.pending.{pending_id}"


def _decode(row: ApplicationSettings) -> PendingSignIn | None:
    try:
        return PendingSignIn(**json.loads(row.value or "null"))
    except (TypeError, ValueError):
        return None


def save(session: Session, pending: PendingSignIn, secret: str) -> None:
    """Store a new pending sign-in and its secret. Commits."""
    secrets_store.store_secret(_secret_ref(pending.id), {"value": secret})
    session.add(ApplicationSettings(name=_name(pending.id), type=SETTING_TYPE, value=json.dumps(asdict(pending))))
    session.commit()


def load(session: Session, pending_id: str | None) -> PendingSignIn | None:
    """The pending sign-in, or None if there's none (or it expired, which removes it)."""
    if not pending_id:
        return None
    row = session.query(ApplicationSettings).filter(ApplicationSettings.name == _name(pending_id)).first()
    pending = _decode(row) if row is not None else None
    if row is not None and (pending is None or pending.expired):
        discard(session, pending_id)
        return None
    return pending


def device_code(pending_id: str) -> str | None:
    """A device-code sign-in's secret, without taking it (polling needs it many times)."""
    return (secrets_store.load_secret(_secret_ref(pending_id)) or {}).get("value")


def update(session: Session, pending: PendingSignIn) -> None:
    """Save a pending sign-in's changed state (the next poll time). Commits."""
    row = session.query(ApplicationSettings).filter(ApplicationSettings.name == _name(pending.id)).first()
    if row is not None:
        row.value = json.dumps(asdict(pending))
        session.commit()


def claim(session: Session, pending_id: str) -> tuple[PendingSignIn, str] | None:
    """Take a pending sign-in and its secret, once: the row is deleted by a
    conditional DELETE, so only one caller gets it. None if it's gone or expired.
    Commits."""
    pending = load(session, pending_id)
    if pending is None:
        return None
    secret = (secrets_store.load_secret(_secret_ref(pending_id)) or {}).get("value")
    result = session.execute(delete(ApplicationSettings).where(ApplicationSettings.name == _name(pending_id)))
    session.commit()
    secrets_store.delete_secret(_secret_ref(pending_id))
    if not result.rowcount or not secret:
        return None
    return pending, secret


def discard(session: Session, pending_id: str) -> None:
    """Forget a pending sign-in (refused, expired, or finished). Commits."""
    session.execute(delete(ApplicationSettings).where(ApplicationSettings.name == _name(pending_id)))
    session.commit()
    secrets_store.delete_secret(_secret_ref(pending_id))


def purge_expired(session: Session) -> int:
    """Remove pending sign-ins that ran out of time. Returns how many."""
    rows = session.query(ApplicationSettings).filter(ApplicationSettings.name.like(f"{PREFIX}%")).all()
    expired = [row.name[len(PREFIX):] for row in rows if (_decode(row) is None or _decode(row).expired)]
    for pending_id in expired:
        discard(session, pending_id)
    return len(expired)
