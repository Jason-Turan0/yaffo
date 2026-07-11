"""Signed cross-device messages.

Hub signaling carries no authentication of its own, so anything with side
effects (revocation notices now; pull requests in Phase 4) is signed by the
sender's device key and verified against the pubkey in the *recipient's own*
trust store — never against anything the message carries. An unsigned
"you're revoked" would let any hub client sabotage other pairings.

DB-free by design: verifiers take a `lookup` callable so the trust store can
be the known_devices table (production) or a plain dict (tests).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from yaffo.p2p.identity import DeviceIdentity
from yaffo.p2p.pairing import sign_nonce, verify_nonce_signature

SIGNED_MESSAGE_MAX_AGE_SECONDS = 300

MESSAGE_REVOKED = "revoked"

TRUST_STATE_TRUSTED = "trusted"


@dataclass
class PeerRecord:
    """What a verifier needs to know about the claimed sender, straight from
    the local trust store."""

    pubkey: str
    trust_state: str


PeerLookup = Callable[[str], Optional[PeerRecord]]


def _canonical(message_type: str, device_id: str, timestamp: int) -> str:
    return f"{message_type}|{device_id}|{timestamp}"


def build_signed_message(identity: DeviceIdentity, message_type: str) -> dict:
    """A message another device can attribute to us: our device_id, a
    timestamp (replay bound), and an ECDSA signature over both — verified
    by the recipient against the pubkey in *its* trust store.
    """
    timestamp = int(time.time())
    return {
        "type": message_type,
        "device_id": identity.device_id,
        "ts": timestamp,
        "signature": sign_nonce(identity.private_key, _canonical(message_type, identity.device_id, timestamp)),
    }


def verify_signed_message(
    body: dict, lookup: PeerLookup, message_type: str, require_trusted: bool = True
) -> Optional[str]:
    """Returns an error message, or None if the message is authentic."""
    sender = body.get("device_id")
    known = lookup(sender) if sender else None
    if known is None or (require_trusted and known.trust_state != TRUST_STATE_TRUSTED):
        return f"{sender} is not a trusted device — pair first"

    timestamp = body.get("ts")
    if not isinstance(timestamp, int) or abs(time.time() - timestamp) > SIGNED_MESSAGE_MAX_AGE_SECONDS:
        return "request timestamp missing or stale"

    if not verify_nonce_signature(known.pubkey, _canonical(message_type, sender, timestamp), body.get("signature", "")):
        return "request signature verification failed"

    return None


def build_revocation_notice(identity: DeviceIdentity) -> dict:
    """Courtesy notice sent over the hub when revoking a peer, so their UI
    can say "revoked" instead of leaving them to discover it via failing
    requests. Enforcement never depends on it arriving — the local trust
    store is what every request is checked against."""
    return build_signed_message(identity, MESSAGE_REVOKED)


def verify_revocation_notice(body: dict, lookup: PeerLookup) -> Optional[str]:
    """Trust is NOT required — only that the sender is a known device whose
    stored pubkey verifies the signature (a revoked-marked row may see a
    repeat notice; that's fine and idempotent)."""
    return verify_signed_message(body, lookup, MESSAGE_REVOKED, require_trusted=False)
