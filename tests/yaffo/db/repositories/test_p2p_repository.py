"""Repository tests for the p2p trust store (known_devices) and share grants:
the trusted → revoked → re-paired lifecycle, and the grant shape rule +
soft revocation."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import (
    GRANT_SCOPE_ALBUM,
    GRANT_SCOPE_FOLDER,
    GRANT_SCOPE_MEDIA_DIR,
    TRUST_STATE_REVOKED,
    TRUST_STATE_TRUSTED,
)
from yaffo.db.repositories import p2p_repository as repo

pytestmark = pytest.mark.unit

DEVICE = "AAAA-BBBB-CCCC-DDDD"
PUBKEY = "fake-pubkey-b64"


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


@pytest.fixture
def paired(session):
    return repo.upsert_trusted_device(session, DEVICE, PUBKEY, "living-room-mac")


def test_upsert_records_a_trusted_peer(session, paired):
    device = repo.get_known_device(session, DEVICE)
    assert device.trust_state == TRUST_STATE_TRUSTED
    assert device.pubkey == PUBKEY
    assert device.display_name == "living-room-mac"
    assert device.paired_at is not None
    assert device.revoked_at is None


def test_mark_revoked_flips_state_and_keeps_the_row(session, paired):
    assert repo.mark_device_revoked(session, DEVICE) is True
    device = repo.get_known_device(session, DEVICE)
    assert device.trust_state == TRUST_STATE_REVOKED
    assert device.revoked_at is not None


def test_mark_revoked_never_creates_a_row(session):
    assert repo.mark_device_revoked(session, "NOBODY-HOME") is False
    assert repo.list_known_devices(session) == []


def test_repairing_a_revoked_device_restores_trust(session, paired):
    repo.mark_device_revoked(session, DEVICE)
    repo.upsert_trusted_device(session, DEVICE, PUBKEY, "renamed")
    device = repo.get_known_device(session, DEVICE)
    assert device.trust_state == TRUST_STATE_TRUSTED
    assert device.revoked_at is None
    assert device.display_name == "renamed"


def test_touch_last_seen(session, paired):
    assert repo.get_known_device(session, DEVICE).last_seen_at is None
    repo.touch_last_seen(session, DEVICE)
    assert repo.get_known_device(session, DEVICE).last_seen_at is not None
    repo.touch_last_seen(session, "NOBODY-HOME")  # no-op, no error


# ---- share grants -----------------------------------------------------------


def test_media_dir_grant_shape(session, paired):
    grant = repo.create_grant(session, DEVICE, GRANT_SCOPE_MEDIA_DIR, media_dir_id="guid-1")
    assert grant.relative_path is None
    assert [g.id for g in repo.list_active_grants(session, DEVICE)] == [grant.id]


def test_folder_grant_shape(session, paired):
    grant = repo.create_grant(
        session, DEVICE, GRANT_SCOPE_FOLDER, media_dir_id="guid-1", relative_path="2024/summer"
    )
    assert grant.relative_path == "2024/summer"


@pytest.mark.parametrize(
    "scope,kwargs",
    [
        (GRANT_SCOPE_MEDIA_DIR, {}),  # missing media_dir_id
        (GRANT_SCOPE_MEDIA_DIR, {"media_dir_id": "g", "relative_path": "x"}),  # extra path
        (GRANT_SCOPE_FOLDER, {"media_dir_id": "g"}),  # missing relative_path
        (GRANT_SCOPE_ALBUM, {}),  # not until Phase 6
        ("bogus", {"media_dir_id": "g"}),
    ],
)
def test_invalid_grant_shapes_are_rejected(session, paired, scope, kwargs):
    with pytest.raises(ValueError):
        repo.create_grant(session, DEVICE, scope, **kwargs)


def test_grant_requires_a_known_device(session):
    with pytest.raises(ValueError, match="not a known device"):
        repo.create_grant(session, "NOBODY-HOME", GRANT_SCOPE_MEDIA_DIR, media_dir_id="guid-1")


def test_revoke_grant_is_soft_and_idempotent(session, paired):
    grant = repo.create_grant(session, DEVICE, GRANT_SCOPE_MEDIA_DIR, media_dir_id="guid-1")
    assert repo.revoke_grant(session, grant.id) is True
    assert repo.list_active_grants(session, DEVICE) == []
    # The row survives (history), just inactive.
    assert grant.revoked_at is not None
    first_revoked_at = grant.revoked_at
    assert repo.revoke_grant(session, grant.id) is True
    assert grant.revoked_at == first_revoked_at
    assert repo.revoke_grant(session, 99999) is False
