"""Pairing codes and the nonce signatures that prove key possession.

A pairing code is the out-of-band trust anchor: everything a joining device
needs to verify the initiating device (its ID and pubkey) plus a single-use
nonce the joiner signs to prove it holds the key behind the identity it
claims. Unlike the POC, no host/port is embedded — the confirm exchange rides
the relay-first call flow, so no address is needed (or trusted).
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import asdict, dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from yaffo.p2p.identity import CURVE

PAIRING_CODE_TTL_SECONDS = 300


class PairingError(Exception):
    pass


@dataclass
class PairingCode:
    """Copy-pasted (or QR-scanned) between devices by a human — the moment
    that human vouches "this is really my other device" is the trust anchor;
    no server mints or approves it."""

    v: int
    device_id: str
    pubkey: str  # urlsafe-b64 uncompressed-point ECDSA P-256 public key
    nonce: str  # urlsafe-b64 random bytes, single-use
    expires_at: float

    def encode(self) -> str:
        raw = json.dumps(asdict(self), separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @staticmethod
    def decode(text: str) -> "PairingCode":
        try:
            raw = base64.urlsafe_b64decode(text.strip().encode("ascii"))
            data = json.loads(raw)
            return PairingCode(**data)
        except Exception as exc:
            raise PairingError("malformed pairing code") from exc

    def is_expired(self) -> bool:
        return time.time() > self.expires_at


def new_pairing_code(device_id: str, pubkey_b64: str) -> PairingCode:
    nonce = base64.urlsafe_b64encode(os.urandom(16)).decode("ascii")
    return PairingCode(
        v=1,
        device_id=device_id,
        pubkey=pubkey_b64,
        nonce=nonce,
        expires_at=time.time() + PAIRING_CODE_TTL_SECONDS,
    )


def sign_nonce(private_key: ec.EllipticCurvePrivateKey, nonce_b64: str) -> str:
    """Proof of private-key possession: the joining device signs the nonce
    from the pairing code so the initiator can verify it, not just trust a
    claimed device_id in a request body.
    """
    signature = private_key.sign(nonce_b64.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    return base64.urlsafe_b64encode(signature).decode("ascii")


def verify_nonce_signature(pubkey_b64: str, nonce_b64: str, signature_b64: str) -> bool:
    try:
        pub_bytes = base64.urlsafe_b64decode(pubkey_b64.encode("ascii"))
        signature = base64.urlsafe_b64decode(signature_b64.encode("ascii"))
        public_key = ec.EllipticCurvePublicKey.from_encoded_point(CURVE, pub_bytes)
        public_key.verify(signature, nonce_b64.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False
