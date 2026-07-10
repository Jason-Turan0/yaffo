from __future__ import annotations

import base64
import hashlib
import os

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

# Challenge-response authentication for the signaling WebSocket. Device IDs
# are self-authenticating — the ID *is* a hash of the public key — so the hub
# can verify "this connection really is device X" with no account registry:
# check that the claimed ID derives from the presented pubkey, then check a
# signature over a hub-issued nonce (proof of key possession, so a pubkey
# sniffed off a pairing code can't be replayed by someone without the key).
#
# The derivation and signature scheme must stay byte-for-byte identical to the
# device side (p2p_poc/identity.py + pairing.py today, yaffo/p2p/ in Phase 2).
# The hub is deliberately dependency-free of those packages — it deploys
# alone — so the ~20 lines are duplicated here and pinned by tests.

CURVE = ec.SECP256R1()
NONCE_BYTES = 32


def device_id_from_pubkey(pubkey_bytes: bytes) -> str:
    """Same dash-grouped base32 hash the devices derive (identity.py)."""
    digest = hashlib.sha256(pubkey_bytes).digest()
    encoded = base64.b32encode(digest).decode("ascii").rstrip("=")[:16]
    return "-".join(encoded[i : i + 4] for i in range(0, len(encoded), 4))


def new_challenge_nonce() -> str:
    return base64.urlsafe_b64encode(os.urandom(NONCE_BYTES)).decode("ascii")


def verify_auth(device_id: str, pubkey_b64: str, nonce_b64: str, signature_b64: str) -> bool:
    """True iff `pubkey` hashes to `device_id` AND `signature` is a valid
    ECDSA-SHA256 signature over the nonce string by that key. Both checks are
    against values the hub itself issued or derived — nothing in the client's
    message is trusted on its own.
    """
    try:
        pubkey_bytes = base64.urlsafe_b64decode(pubkey_b64.encode("ascii"))
        signature = base64.urlsafe_b64decode(signature_b64.encode("ascii"))
        public_key = ec.EllipticCurvePublicKey.from_encoded_point(CURVE, pubkey_bytes)
    except (ValueError, TypeError):
        return False
    if device_id_from_pubkey(pubkey_bytes) != device_id:
        return False
    try:
        public_key.verify(signature, nonce_b64.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False
