"""This device's p2p identity: an ECDSA P-256 keypair and the values derived
from it (device ID, self-signed TLS certificate).

The private key lives in the OS keychain via `keyring` (the secrets-storage
convention — same "yaffo" service as the API keys, never the SQLite settings
DB and never a file). The certificate and device_id are re-derived from it on
every startup: peers pin by public-key hash, so a freshly minted cert with the
same key is the same identity.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import ipaddress
import os
from dataclasses import dataclass
from typing import Optional, Protocol

import keyring
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from keyring.errors import KeyringError

# ECDSA P-256, not Ed25519: Chrome/BoringSSL rejects Ed25519 as a certificate
# signature algorithm (the POC captured the ClientHello — see the design doc's
# "What the POC proved"). P-256 is accepted by every mainstream TLS stack and
# is what Syncthing uses for its device certs.
CURVE = ec.SECP256R1()

_KEYRING_SERVICE = "yaffo"


def _keyring_key() -> str:
    """Keychain account name for this install's device key. Scoped to the
    data dir (not a fixed name) because identity is per-install: two Yaffo
    instances on one machine — e.g. a second dev instance under a different
    YAFFO_DATA_DIR — must be distinct devices that can pair with each other."""
    from yaffo.common import ROOT_DIR

    return f"p2p_device_key:{ROOT_DIR}"


class SecretStore(Protocol):
    """Where the private-key PEM lives. Production uses the OS keychain;
    tests inject an in-memory store so they never touch (or prompt for) the
    real keychain."""

    def get(self) -> Optional[str]: ...

    def set(self, value: str) -> None: ...


class KeyringSecretStore:
    def get(self) -> Optional[str]:
        try:
            return keyring.get_password(_KEYRING_SERVICE, _keyring_key())
        except KeyringError:
            return None

    def set(self, value: str) -> None:
        keyring.set_password(_KEYRING_SERVICE, _keyring_key(), value)


class InMemorySecretStore:
    """Test double; also the honest fallback if the keychain is unavailable
    (the identity then lasts one process lifetime instead of crashing p2p)."""

    def __init__(self) -> None:
        self._value: Optional[str] = None

    def get(self) -> Optional[str]:
        return self._value

    def set(self, value: str) -> None:
        self._value = value


def _device_id_from_pubkey(pubkey_bytes: bytes) -> str:
    """Human-shareable identity: a short, dash-grouped hash of the raw public
    key. This is the whole trust model — there is no CA. Two devices consider
    each other authenticated once this value (independently derived by each
    side from the other's certificate/public key) matches what was agreed
    during pairing. Self-authenticating: the hub verifies claimed IDs the
    same way, with no registry.
    """
    digest = hashlib.sha256(pubkey_bytes).digest()
    encoded = base64.b32encode(digest).decode("ascii").rstrip("=")[:16]
    return "-".join(encoded[i : i + 4] for i in range(0, len(encoded), 4))


def _raw_public_bytes(public_key: ec.EllipticCurvePublicKey) -> bytes:
    return public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


@dataclass
class DeviceIdentity:
    private_key: ec.EllipticCurvePrivateKey
    device_id: str
    certificate: x509.Certificate  # self-signed, derived at startup; never persisted

    @property
    def public_key_b64(self) -> str:
        return base64.urlsafe_b64encode(_raw_public_bytes(self.private_key.public_key())).decode("ascii")


def device_id_from_pubkey_b64(pubkey_b64: str) -> str:
    """Derive the device ID a transmitted pubkey *actually* corresponds to.
    IDs are self-authenticating: any claimed (device_id, pubkey) pair is
    checked with this before being recorded or dialed, so a mismatched pair
    is rejected without any registry."""
    return _device_id_from_pubkey(base64.urlsafe_b64decode(pubkey_b64.encode("ascii")))


def fingerprint_of_x509_cert(cert: x509.Certificate) -> str:
    """Derive the same device_id from a certificate seen on the wire.

    Used to pin a peer's certificate against the device_id embedded in a
    pairing code / trust store, instead of validating against a CA (there
    isn't one).
    """
    return _device_id_from_pubkey(_raw_public_bytes(cert.public_key()))


def load_or_create_identity(secret_store: Optional[SecretStore] = None) -> DeviceIdentity:
    """Load this device's persistent identity, generating one on first run.

    The keypair is generated once and kept for the life of the install, not
    re-issued per connection. The certificate is minted fresh each call —
    pinning is by public-key hash, so its serial/validity don't matter.
    """
    # YAFFO_P2P_EPHEMERAL_IDENTITY=1 (the UI-test sandbox) keeps the key in
    # memory: throwaway temp-dir instances would otherwise leave one keychain
    # entry behind per run, since the account name is scoped to the data dir.
    if secret_store is None and os.environ.get("YAFFO_P2P_EPHEMERAL_IDENTITY") == "1":
        secret_store = InMemorySecretStore()
    store = secret_store or KeyringSecretStore()
    pem = store.get()
    if pem:
        private_key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    else:
        private_key = ec.generate_private_key(CURVE)
        store.set(
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode("ascii")
        )

    return DeviceIdentity(
        private_key=private_key,
        device_id=_device_id_from_pubkey(_raw_public_bytes(private_key.public_key())),
        certificate=_self_signed_cert(private_key),
    )


def _self_signed_cert(private_key: ec.EllipticCurvePrivateKey) -> x509.Certificate:
    # SANs only matter to clients doing hostname verification; the pinning
    # QUIC client never does. localhost/127.0.0.1 are included so a browser
    # hitting the device directly (a possible future) can at least negotiate.
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "yaffo-p2p")])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                key_cert_sign=False,
                content_commitment=False,
                key_agreement=False,
                data_encipherment=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    )
    return builder.sign(private_key, hashes.SHA256())
