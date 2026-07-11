"""Persistence for P2P device sharing: the Known Devices trust store and
share grants (docs/development/p2p-sharing.md). Replaces the POC's JSON-file
store. The trust store is what every incoming p2p request is verified
against — the trust anchor is always the local pairing record, never
anything a request carries.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from yaffo.db.models import (
    GRANT_SCOPE_ALBUM,
    GRANT_SCOPE_FOLDER,
    GRANT_SCOPE_MEDIA_DIR,
    TRUST_STATE_REVOKED,
    TRUST_STATE_TRUSTED,
    KnownDevice,
    ShareGrant,
)
from yaffo.utils.time import utcnow


# ---- known devices (the trust store) ---------------------------------------

def list_known_devices(session: Session) -> list[KnownDevice]:
    return session.query(KnownDevice).order_by(KnownDevice.paired_at).all()


def get_known_device(session: Session, device_id: str) -> Optional[KnownDevice]:
    return session.get(KnownDevice, device_id)


def upsert_trusted_device(
    session: Session, device_id: str, pubkey: str, display_name: str
) -> KnownDevice:
    """Record a successful pairing. Re-pairing an existing (possibly revoked)
    row overwrites it back to trusted — a fresh code is fresh human consent."""
    device = session.get(KnownDevice, device_id)
    if device is None:
        device = KnownDevice(device_id=device_id)
        session.add(device)
    device.pubkey = pubkey
    device.display_name = display_name
    device.trust_state = TRUST_STATE_TRUSTED
    device.paired_at = utcnow()
    device.revoked_at = None
    session.commit()
    return device


def mark_device_revoked(session: Session, device_id: str) -> bool:
    """Flip a peer to revoked — the enforcement flag every request re-checks.
    Used both when the user revokes a peer and when a verified revocation
    notice arrives (the row stays visible so the UI can say why things
    stopped working). Never creates a row; idempotent. Returns whether the
    device was known."""
    device = session.get(KnownDevice, device_id)
    if device is None:
        return False
    if device.trust_state != TRUST_STATE_REVOKED:
        device.trust_state = TRUST_STATE_REVOKED
        device.revoked_at = utcnow()
        session.commit()
    return True


def rename_device(session: Session, device_id: str, display_name: str) -> bool:
    """Display names are peer-supplied at pairing but locally editable (a
    re-pair overwrites with the peer's current name again). Returns whether
    the device was known."""
    device = session.get(KnownDevice, device_id)
    if device is None:
        return False
    device.display_name = display_name
    session.commit()
    return True


def touch_last_seen(session: Session, device_id: str) -> None:
    """Record evidence the peer is alive (a successful authenticated
    exchange). Presence itself is never persisted — this is display-only."""
    device = session.get(KnownDevice, device_id)
    if device is not None:
        device.last_seen_at = utcnow()
        session.commit()


# ---- share grants -----------------------------------------------------------

_GRANT_SHAPES = {
    # scope_type -> (media_dir_id required, relative_path required)
    GRANT_SCOPE_MEDIA_DIR: (True, False),
    GRANT_SCOPE_FOLDER: (True, True),
}


def create_grant(
    session: Session,
    peer_device_id: str,
    scope_type: str,
    media_dir_id: Optional[str] = None,
    relative_path: Optional[str] = None,
) -> ShareGrant:
    """Authorize one trusted peer for one scope. Enforces the shape rule:
    media_dir ⇒ media_dir_id only; folder ⇒ media_dir_id + relative_path.
    (The album scope arrives with migration 007 / Phase 6.)"""
    if scope_type == GRANT_SCOPE_ALBUM:
        raise ValueError("album grants arrive in Phase 6")
    if scope_type not in _GRANT_SHAPES:
        raise ValueError(f"unknown grant scope type: {scope_type!r}")
    needs_dir, needs_path = _GRANT_SHAPES[scope_type]
    if bool(media_dir_id) != needs_dir or bool(relative_path) != needs_path:
        raise ValueError(f"grant shape invalid for scope {scope_type!r}")
    if get_known_device(session, peer_device_id) is None:
        raise ValueError(f"{peer_device_id} is not a known device")

    grant = ShareGrant(
        peer_device_id=peer_device_id,
        scope_type=scope_type,
        media_dir_id=media_dir_id,
        relative_path=relative_path,
    )
    session.add(grant)
    session.commit()
    return grant


def list_active_grants(session: Session, peer_device_id: Optional[str] = None) -> list[ShareGrant]:
    query = session.query(ShareGrant).filter(ShareGrant.revoked_at.is_(None))
    if peer_device_id is not None:
        query = query.filter(ShareGrant.peer_device_id == peer_device_id)
    return query.order_by(ShareGrant.created_at).all()


def revoke_grant(session: Session, grant_id: int) -> bool:
    """Soft-revoke (an update, not a delete, so the UI can show history);
    takes effect on the peer's next request. Idempotent."""
    grant = session.get(ShareGrant, grant_id)
    if grant is None:
        return False
    if grant.revoked_at is None:
        grant.revoked_at = utcnow()
        session.commit()
    return True
