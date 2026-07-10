import time

from p2p_poc.identity import load_or_create_identity
from p2p_poc.pairing import PairingCode, new_pairing_code, sign_nonce, verify_nonce_signature


def test_pairing_code_round_trip(tmp_path):
    identity = load_or_create_identity(tmp_path)
    code = new_pairing_code(identity.device_id, identity.public_key_b64, "127.0.0.1", 9000)
    assert PairingCode.decode(code.encode()) == code


def test_expired_code_is_detected(tmp_path):
    identity = load_or_create_identity(tmp_path)
    code = new_pairing_code(identity.device_id, identity.public_key_b64, "127.0.0.1", 9000)
    code.expires_at = time.time() - 1
    assert code.is_expired()


def test_signature_round_trip(tmp_path):
    identity = load_or_create_identity(tmp_path)
    nonce = "abc123"
    signature = sign_nonce(identity.private_key, nonce)
    assert verify_nonce_signature(identity.public_key_b64, nonce, signature)


def test_signature_from_wrong_key_is_rejected(tmp_path):
    """Simulates an impersonator claiming to be device B without holding its key."""
    identity_a = load_or_create_identity(tmp_path / "a")
    identity_b = load_or_create_identity(tmp_path / "b")
    nonce = "abc123"
    signature = sign_nonce(identity_a.private_key, nonce)
    assert not verify_nonce_signature(identity_b.public_key_b64, nonce, signature)
