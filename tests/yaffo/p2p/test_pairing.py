"""Pairing codes: round-trip encoding, expiry, and the nonce signatures that
prove key possession (the check that stops a merely *claimed* device_id)."""
import time

import pytest

from yaffo.p2p.identity import InMemorySecretStore, load_or_create_identity
from yaffo.p2p.pairing import (
    PairingCode,
    PairingError,
    new_pairing_code,
    sign_nonce,
    verify_nonce_signature,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def identity():
    return load_or_create_identity(InMemorySecretStore())


def test_pairing_code_round_trip(identity):
    code = new_pairing_code(identity.device_id, identity.public_key_b64)
    assert PairingCode.decode(code.encode()) == code


def test_malformed_code_raises(identity):
    with pytest.raises(PairingError):
        PairingCode.decode("not-a-code")


def test_expired_code_is_detected(identity):
    code = new_pairing_code(identity.device_id, identity.public_key_b64)
    assert not code.is_expired()
    code.expires_at = time.time() - 1
    assert code.is_expired()


def test_nonces_are_single_use_material(identity):
    """Two codes never share a nonce — the nonce is what burns on first use."""
    first = new_pairing_code(identity.device_id, identity.public_key_b64)
    second = new_pairing_code(identity.device_id, identity.public_key_b64)
    assert first.nonce != second.nonce


def test_signature_round_trip(identity):
    nonce = "abc123"
    signature = sign_nonce(identity.private_key, nonce)
    assert verify_nonce_signature(identity.public_key_b64, nonce, signature)


def test_signature_from_wrong_key_is_rejected():
    """Simulates an impersonator claiming to be device B without holding its key."""
    identity_a = load_or_create_identity(InMemorySecretStore())
    identity_b = load_or_create_identity(InMemorySecretStore())
    nonce = "abc123"
    signature = sign_nonce(identity_a.private_key, nonce)
    assert not verify_nonce_signature(identity_b.public_key_b64, nonce, signature)
