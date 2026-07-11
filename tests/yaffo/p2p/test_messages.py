"""Signed cross-device messages: attribution against the recipient's OWN
trust store (never anything the message carries), replay bounds, and the
trust-not-required rule for revocation notices."""
import pytest

from yaffo.p2p.handlers.pairing import build_revocation_notice, verify_revocation_notice
from yaffo.p2p.handlers.pull_file import build_pull_file_request, verify_pull_file_request
from yaffo.p2p.identity import InMemorySecretStore, load_or_create_identity
from yaffo.p2p.messages import PeerRecord, build_signed_message, verify_signed_message

pytestmark = pytest.mark.unit


@pytest.fixture
def sender():
    return load_or_create_identity(InMemorySecretStore())


def _store(*records: tuple) -> callable:
    table = {device_id: PeerRecord(pubkey=pubkey, trust_state=state) for device_id, pubkey, state in records}
    return table.get


def test_valid_message_from_trusted_peer(sender):
    body = build_signed_message(sender, "pull_files")
    lookup = _store((sender.device_id, sender.public_key_b64, "trusted"))
    assert verify_signed_message(body, lookup, "pull_files") is None


def test_unknown_sender_is_rejected(sender):
    body = build_signed_message(sender, "pull_files")
    assert "not a trusted device" in verify_signed_message(body, _store(), "pull_files")


def test_revoked_sender_is_rejected_when_trust_required(sender):
    body = build_signed_message(sender, "pull_files")
    lookup = _store((sender.device_id, sender.public_key_b64, "revoked"))
    assert "not a trusted device" in verify_signed_message(body, lookup, "pull_files")


def test_stale_timestamp_is_rejected(sender):
    body = build_signed_message(sender, "pull_files")
    body["ts"] -= 3600  # outside the replay bound (also breaks the signature, but ts is checked first)
    lookup = _store((sender.device_id, sender.public_key_b64, "trusted"))
    assert "stale" in verify_signed_message(body, lookup, "pull_files")


def test_signature_from_wrong_key_is_rejected(sender):
    imposter = load_or_create_identity(InMemorySecretStore())
    body = build_signed_message(imposter, "pull_files")
    body["device_id"] = sender.device_id  # claims to be sender, signed by imposter
    lookup = _store((sender.device_id, sender.public_key_b64, "trusted"))
    assert "signature" in verify_signed_message(body, lookup, "pull_files")


def test_type_mismatch_breaks_signature(sender):
    """The message type is part of the canonical string, so a signed ping
    can't be replayed as something with side effects."""
    body = build_signed_message(sender, "pull_files")
    lookup = _store((sender.device_id, sender.public_key_b64, "trusted"))
    assert verify_signed_message(body, lookup, "revoked") is not None


def test_pull_file_signature_covers_requested_path(sender):
    body = build_pull_file_request(sender, "media-1", "2024/a.jpg", 0, 1024)
    lookup = _store((sender.device_id, sender.public_key_b64, "trusted"))
    assert verify_pull_file_request(body, lookup) is None
    body["relative_path"] = "private/a.jpg"
    assert "signature" in verify_pull_file_request(body, lookup)


def test_revocation_notice_verifies_without_trust(sender):
    """A revoked-marked row may see a repeat notice; only the stored pubkey
    must verify, not the trust state."""
    notice = build_revocation_notice(sender)
    lookup = _store((sender.device_id, sender.public_key_b64, "revoked"))
    assert verify_revocation_notice(notice, lookup) is None


def test_unsigned_revocation_notice_is_rejected(sender):
    """The reason notices are signed at all: an unsigned 'you're revoked'
    would let any hub client sabotage other pairings."""
    lookup = _store((sender.device_id, sender.public_key_b64, "trusted"))
    forged = {"type": "revoked", "device_id": sender.device_id, "ts": 0, "signature": ""}
    assert verify_revocation_notice(forged, lookup) is not None
