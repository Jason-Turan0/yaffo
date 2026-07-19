"""Device identity: keypair persistence (via the injected secret store — the
real one is the OS keychain, which tests never touch), self-authenticating
device IDs, and certificate fingerprint derivation."""
import stat

import pytest

from yaffo.p2p.identity import (
    FileSecretStore,
    InMemorySecretStore,
    device_id_from_pubkey_b64,
    fingerprint_of_x509_cert,
    load_or_create_identity,
)

pytestmark = pytest.mark.unit


def test_device_id_is_stable_across_reloads():
    store = InMemorySecretStore()
    first = load_or_create_identity(store)
    second = load_or_create_identity(store)
    assert first.device_id == second.device_id
    assert first.public_key_b64 == second.public_key_b64


def test_different_stores_get_different_identities():
    device_a = load_or_create_identity(InMemorySecretStore())
    device_b = load_or_create_identity(InMemorySecretStore())
    assert device_a.device_id != device_b.device_id


def test_certificate_fingerprint_matches_device_id():
    """The pin: the device_id derived from the cert seen on the wire must
    equal the one derived from the keypair — including across a reload,
    where the cert is minted fresh but the key is the same."""
    store = InMemorySecretStore()
    identity = load_or_create_identity(store)
    assert fingerprint_of_x509_cert(identity.certificate) == identity.device_id
    reloaded = load_or_create_identity(store)
    assert fingerprint_of_x509_cert(reloaded.certificate) == identity.device_id


def test_device_id_from_transmitted_pubkey():
    """Self-authenticating IDs: a (device_id, pubkey) pair from the wire can
    be checked with no registry."""
    identity = load_or_create_identity(InMemorySecretStore())
    other = load_or_create_identity(InMemorySecretStore())
    assert device_id_from_pubkey_b64(identity.public_key_b64) == identity.device_id
    assert device_id_from_pubkey_b64(other.public_key_b64) != identity.device_id


def test_file_secret_store_persists_identity_with_private_permissions(tmp_path):
    key_file = tmp_path / "identity" / "device-key.pem"

    first = load_or_create_identity(FileSecretStore(key_file))
    second = load_or_create_identity(FileSecretStore(key_file))

    assert first.device_id == second.device_id
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_file.parent.stat().st_mode) == 0o700


def test_identity_uses_configured_headless_key_file(tmp_path, monkeypatch):
    key_file = tmp_path / "device-key.pem"
    monkeypatch.setenv("YAFFO_P2P_KEY_FILE", str(key_file))

    first = load_or_create_identity()
    second = load_or_create_identity()

    assert first.device_id == second.device_id
    assert key_file.exists()
