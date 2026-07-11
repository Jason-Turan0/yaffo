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

import json
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from yaffo.p2p.identity import DeviceIdentity
from yaffo.p2p.pairing import sign_nonce, verify_nonce_signature

SIGNED_MESSAGE_MAX_AGE_SECONDS = 300

MESSAGE_LIST_SHARED = "list_shared"
MESSAGE_LIST_FILES = "list_files"
MESSAGE_PULL_PREVIEW = "pull_preview"
MESSAGE_PULL_FILE = "pull_file"
MESSAGE_REVOKED = "revoked"

TRUST_STATE_TRUSTED = "trusted"


@dataclass
class PeerRecord:
    """What a verifier needs to know about the claimed sender, straight from
    the local trust store."""

    pubkey: str
    trust_state: str


PeerLookup = Callable[[str], Optional[PeerRecord]]


def _canonical(message_type: str, device_id: str, timestamp: int, payload: Optional[dict] = None) -> str:
    if payload is None:
        return f"{message_type}|{device_id}|{timestamp}"
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{message_type}|{device_id}|{timestamp}|{encoded}"


def build_signed_message(identity: DeviceIdentity, message_type: str, payload: Optional[dict] = None) -> dict:
    """A message another device can attribute to us: our device_id, a
    timestamp (replay bound), and an ECDSA signature over both — verified
    by the recipient against the pubkey in *its* trust store.
    """
    timestamp = int(time.time())
    signed = {
        "type": message_type,
        "device_id": identity.device_id,
        "ts": timestamp,
        "signature": sign_nonce(identity.private_key, _canonical(message_type, identity.device_id, timestamp, payload)),
    }
    if payload:
        signed.update(payload)
    return signed


def verify_signed_message(
    body: dict,
    lookup: PeerLookup,
    message_type: str,
    require_trusted: bool = True,
    signed_fields: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Returns an error message, or None if the message is authentic."""
    sender = body.get("device_id")
    known = lookup(sender) if sender else None
    if known is None or (require_trusted and known.trust_state != TRUST_STATE_TRUSTED):
        return f"{sender} is not a trusted device — pair first"

    timestamp = body.get("ts")
    if not isinstance(timestamp, int) or abs(time.time() - timestamp) > SIGNED_MESSAGE_MAX_AGE_SECONDS:
        return "request timestamp missing or stale"

    payload = None
    if signed_fields is not None:
        payload = {field: body.get(field) for field in signed_fields}

    if not verify_nonce_signature(
        known.pubkey, _canonical(message_type, sender, timestamp, payload), body.get("signature", "")
    ):
        return "request signature verification failed"

    return None


def build_list_shared_request(identity: DeviceIdentity) -> dict:
    return build_signed_message(identity, MESSAGE_LIST_SHARED)


def verify_list_shared_request(body: dict, lookup: PeerLookup) -> Optional[str]:
    return verify_signed_message(body, lookup, MESSAGE_LIST_SHARED)


def build_list_files_request(
    identity: DeviceIdentity, media_dir_id: str, relative_path: str, filters: dict, offset: int, limit: int
) -> dict:
    """One page of a scope's file manifests. `relative_path` is the scope
    being browsed ("" for a whole media dir); `filters` is a flat dict of
    optional criteria (path substring, media_type, year, month, device) the
    serving side applies *after* scoping to the grant."""
    return build_signed_message(
        identity,
        MESSAGE_LIST_FILES,
        {
            "media_dir_id": media_dir_id,
            "relative_path": relative_path,
            "filters": filters,
            "offset": offset,
            "limit": limit,
        },
    )


def verify_list_files_request(body: dict, lookup: PeerLookup) -> Optional[str]:
    return verify_signed_message(
        body,
        lookup,
        MESSAGE_LIST_FILES,
        signed_fields=("media_dir_id", "relative_path", "filters", "offset", "limit"),
    )


def build_pull_preview_request(
    identity: DeviceIdentity, media_dir_id: str, relative_path: str, max_dimension: int
) -> dict:
    """A downscaled, recompressed preview of one shared file — what the
    remote gallery shows instead of pulling originals."""
    return build_signed_message(
        identity,
        MESSAGE_PULL_PREVIEW,
        {
            "media_dir_id": media_dir_id,
            "relative_path": relative_path,
            "max_dimension": max_dimension,
        },
    )


def verify_pull_preview_request(body: dict, lookup: PeerLookup) -> Optional[str]:
    return verify_signed_message(
        body,
        lookup,
        MESSAGE_PULL_PREVIEW,
        signed_fields=("media_dir_id", "relative_path", "max_dimension"),
    )


def build_pull_file_request(
    identity: DeviceIdentity, media_dir_id: str, relative_path: str, offset: int, length: int
) -> dict:
    return build_signed_message(
        identity,
        MESSAGE_PULL_FILE,
        {
            "media_dir_id": media_dir_id,
            "relative_path": relative_path,
            "offset": offset,
            "length": length,
        },
    )


def verify_pull_file_request(body: dict, lookup: PeerLookup) -> Optional[str]:
    return verify_signed_message(
        body,
        lookup,
        MESSAGE_PULL_FILE,
        signed_fields=("media_dir_id", "relative_path", "offset", "length"),
    )


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
