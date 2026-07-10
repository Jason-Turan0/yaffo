from __future__ import annotations

import random
import secrets
import time
from pathlib import Path

from .identity import DeviceIdentity
from .pairing import sign_nonce, verify_nonce_signature
from .store import KnownDevice

# The first actual *data* flowing between paired devices: each device seeds
# a handful of small random text files (stand-ins for photos) and serves
# them to trusted peers over the same pinned QUIC exchange as everything
# else. Authorization is a signed request: the puller signs
# "pull_files|<its device_id>|<timestamp>" with its device key, and the
# server verifies that signature against the pubkey in its OWN Known
# Devices store — so the trust anchor is the local pairing record, never
# anything the request carries. An unpaired device can't even be looked
# up, let alone verified. (Transport-level mTLS would be the production
# answer; aioquic's client-certificate support is test-only, and for a
# one-shot request/response stream a signed request is equivalent modulo
# the replay window below.)

SEED_FILE_COUNT = 5
PULL_REQUEST_MAX_AGE_SECONDS = 300

_WORDS = (
    "amber basil cedar dune ember fjord glade harbor iris juniper "
    "kelp lagoon meadow nectar opal pine quartz reef sage tundra"
).split()


def ensure_seed_files(shared_dir: Path, count: int = SEED_FILE_COUNT) -> list[Path]:
    """Create `count` random text files in shared_dir if it has none yet.
    Idempotent across restarts — existing files are kept as-is so peers see
    a stable library.
    """
    shared_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(shared_dir.glob("*.txt"))
    if existing:
        return existing

    created = []
    for _ in range(count):
        name = f"note-{secrets.token_hex(3)}.txt"
        lines = [" ".join(random.choice(_WORDS) for _ in range(8)) for _ in range(3)]
        content = f"{name}\ncreated {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n" + "\n".join(lines) + "\n"
        path = shared_dir / name
        path.write_text(content)
        created.append(path)
    return created


def read_shared_files(shared_dir: Path) -> list[dict]:
    """All shared files with content inline — they're deliberately tiny
    (photo stand-ins), so one pull returns the whole library in a single
    QUIC exchange instead of a connect dance per file.
    """
    files = []
    for path in sorted(shared_dir.glob("*.txt")):
        content = path.read_text()
        files.append({"name": path.name, "size": len(content.encode("utf-8")), "content": content})
    return files


def _canonical(request_type: str, device_id: str, timestamp: int) -> str:
    return f"{request_type}|{device_id}|{timestamp}"


def build_signed_message(identity: DeviceIdentity, message_type: str) -> dict:
    """A message another device can attribute to us: our device_id, a
    timestamp (replay bound), and an ECDSA signature over both — verified
    by the recipient against the pubkey in *its* trust store.
    """
    timestamp = int(time.time())
    return {
        "type": message_type,
        "device_id": identity.device_id,
        "ts": timestamp,
        "signature": sign_nonce(identity.private_key, _canonical(message_type, identity.device_id, timestamp)),
    }


def _verify_signed_message(
    body: dict, known_devices: list[KnownDevice], message_type: str, require_trusted: bool
) -> str | None:
    sender = body.get("device_id")
    known = next((d for d in known_devices if d.device_id == sender), None)
    if known is None or (require_trusted and known.trust_state != "trusted"):
        return f"{sender} is not a trusted device — pair first"

    timestamp = body.get("ts")
    if not isinstance(timestamp, int) or abs(time.time() - timestamp) > PULL_REQUEST_MAX_AGE_SECONDS:
        return "request timestamp missing or stale"

    if not verify_nonce_signature(known.pubkey, _canonical(message_type, sender, timestamp), body.get("signature", "")):
        return "request signature verification failed"

    return None


def build_pull_request(identity: DeviceIdentity) -> dict:
    return build_signed_message(identity, "pull_files")


def verify_pull_request(body: dict, known_devices: list[KnownDevice]) -> str | None:
    """Returns an error message, or None if the request is authorized."""
    return _verify_signed_message(body, known_devices, "pull_files", require_trusted=True)


def build_revocation_notice(identity: DeviceIdentity) -> dict:
    """Courtesy notice sent over the hub when revoking a peer, so their UI
    can show "revoked" instead of leaving them to discover it via failing
    pulls. It must be SIGNED: hub signaling messages carry no
    authentication of their own, and an unsigned "you're revoked" would
    let any device that can reach the hub sabotage other pairings.
    """
    return build_signed_message(identity, "revoked")


def verify_revocation_notice(body: dict, known_devices: list[KnownDevice]) -> str | None:
    """Trust is NOT required — only that the sender is a known device whose
    stored pubkey verifies the signature (a revoked-marked row may see a
    repeat notice; that's fine and idempotent).
    """
    return _verify_signed_message(body, known_devices, "revoked", require_trusted=False)
