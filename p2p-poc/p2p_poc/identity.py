from __future__ import annotations

import base64
import datetime
import hashlib
import ipaddress
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

# ECDSA P-256, not Ed25519: Chrome/BoringSSL rejects Ed25519 as a certificate
# signature algorithm (confirmed via a captured ClientHello — see
# docs/development/p2p-sharing.md's testing notes / README "Known
# limitations"). P-256 is accepted by every mainstream TLS stack and is what
# Syncthing itself uses for its device certs.
CURVE = ec.SECP256R1()


def _device_id_from_pubkey(pubkey_bytes: bytes) -> str:
    """Human-shareable identity: a short, dash-grouped hash of the raw public key.

    This is the whole trust model — there is no CA. Two devices consider each
    other authenticated once this value (independently derived by each side
    from the other's certificate/public key) matches what was agreed during
    pairing.
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
    cert_path: Path
    key_path: Path

    @property
    def public_key_b64(self) -> str:
        return base64.urlsafe_b64encode(_raw_public_bytes(self.private_key.public_key())).decode("ascii")


def fingerprint_of_x509_cert(cert: x509.Certificate) -> str:
    """Derive the same device_id from a certificate seen on the wire.

    Used to pin a peer's certificate against the device_id embedded in a
    pairing code, instead of validating against a CA (there isn't one).
    Takes an already-parsed cert since both callers (aioquic hands one back
    directly after its handshake) have one available without needing DER
    bytes as an intermediate step.
    """
    return _device_id_from_pubkey(_raw_public_bytes(cert.public_key()))


def load_or_create_identity(data_dir: Path, host: str = "127.0.0.1") -> DeviceIdentity:
    """Load this device's persistent identity, generating one on first run.

    Mirrors the design doc: the keypair is generated once and kept for the
    life of the install, not re-issued per connection. `host` is only used
    to populate the certificate's Subject Alternative Name so real browsers
    (which require a matching SAN, unlike the pinning client in
    quic_transport.py) will negotiate TLS at all.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    key_path = data_dir / "device.key"
    cert_path = data_dir / "device.crt"

    if key_path.exists():
        private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    else:
        private_key = ec.generate_private_key(CURVE)
        key_path.write_bytes(
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )

    if not cert_path.exists():
        cert_path.write_bytes(_self_signed_cert(private_key, host).public_bytes(serialization.Encoding.PEM))

    return DeviceIdentity(
        private_key=private_key,
        device_id=_device_id_from_pubkey(_raw_public_bytes(private_key.public_key())),
        cert_path=cert_path,
        key_path=key_path,
    )


def _subject_alt_names(host: str) -> list[x509.GeneralName]:
    # Real browsers (unlike the pinning client, which never checks hostname)
    # require a SAN matching the address they connected to.
    names: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    try:
        parsed = ipaddress.ip_address(host)
        if str(parsed) not in ("127.0.0.1",):
            names.append(x509.IPAddress(parsed))
    except ValueError:
        if host not in ("localhost", "0.0.0.0"):
            names.append(x509.DNSName(host))
    return names


def _self_signed_cert(private_key: ec.EllipticCurvePrivateKey, host: str) -> x509.Certificate:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "yaffo-p2p-poc")])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(_subject_alt_names(host)), critical=False)
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
