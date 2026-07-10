from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import asdict, dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from .identity import CURVE

PAIRING_CODE_TTL_SECONDS = 300


class PairingError(Exception):
    pass


@dataclass
class PairingCode:
    """The out-of-band trust anchor: everything a joining device needs to
    reach and verify the initiating device. Copy-pasted between devices by a
    human, standing in for a QR scan.
    """

    v: int
    device_id: str
    pubkey: str  # urlsafe-b64 uncompressed-point ECDSA P-256 public key
    host: str
    port: int
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


def new_pairing_code(device_id: str, pubkey_b64: str, host: str, port: int) -> PairingCode:
    nonce = base64.urlsafe_b64encode(os.urandom(16)).decode("ascii")
    return PairingCode(
        v=1,
        device_id=device_id,
        pubkey=pubkey_b64,
        host=host,
        port=port,
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
