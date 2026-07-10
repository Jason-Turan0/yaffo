from __future__ import annotations

import base64
import socket
from dataclasses import dataclass

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from yaffo_hub.auth import CURVE, device_id_from_pubkey


@dataclass
class FakeDevice:
    """A device identity as the client side (p2p_poc/identity.py) builds it:
    ECDSA P-256 keypair, urlsafe-b64 uncompressed-point pubkey, hash-derived
    device_id, ECDSA-SHA256 signatures over the ascii nonce string.
    """

    private_key: ec.EllipticCurvePrivateKey

    @property
    def pubkey_bytes(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )

    @property
    def pubkey_b64(self) -> str:
        return base64.urlsafe_b64encode(self.pubkey_bytes).decode("ascii")

    @property
    def device_id(self) -> str:
        return device_id_from_pubkey(self.pubkey_bytes)

    def sign_nonce(self, nonce_b64: str) -> str:
        signature = self.private_key.sign(nonce_b64.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        return base64.urlsafe_b64encode(signature).decode("ascii")

    def auth_reply(self, nonce_b64: str) -> dict:
        return {"type": "auth", "pubkey": self.pubkey_b64, "signature": self.sign_nonce(nonce_b64)}


@pytest.fixture
def device() -> FakeDevice:
    return FakeDevice(ec.generate_private_key(CURVE))


@pytest.fixture
def other_device() -> FakeDevice:
    return FakeDevice(ec.generate_private_key(CURVE))


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
