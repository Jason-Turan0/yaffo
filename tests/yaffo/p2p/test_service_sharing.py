"""Grant-checked P2P serving protocol: signed list_shared / pull_file
requests are verified against the serving device's local trust store, then
scoped through active grants before any indexed media path is exposed."""
import base64
import hashlib
import json

import pytest

from yaffo.app import create_app
from yaffo.db import db
from yaffo.db.models import GRANT_SCOPE_FOLDER, GRANT_SCOPE_MEDIA_DIR, MEDIA_TYPE_PHOTO, MediaItem
from yaffo.db.repositories import media_dir_repository, p2p_repository
from yaffo.p2p.identity import InMemorySecretStore, load_or_create_identity
from yaffo.p2p.messages import build_list_shared_request, build_pull_file_request
from yaffo.p2p.service import P2PService

pytestmark = pytest.mark.unit


@pytest.fixture
def serving_context(tmp_path):
    app = create_app(db_path=tmp_path / "serving.db", config={"TESTING": True})
    service = P2PService(app, secret_store=InMemorySecretStore())
    service.identity = load_or_create_identity(InMemorySecretStore())
    requester = load_or_create_identity(InMemorySecretStore())
    root = tmp_path / "library"
    root.mkdir()

    with app.app_context():
        db.create_all()
        p2p_repository.upsert_trusted_device(
            db.session,
            requester.device_id,
            requester.public_key_b64,
            "requester",
        )
        media_dir = media_dir_repository.add_media_dir(db.session, str(root))
        yield app, service, requester, media_dir, root
        db.session.remove()
        db.drop_all()


def _index_file(path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    db.session.add(MediaItem(full_file_path=str(path), media_type=MEDIA_TYPE_PHOTO))
    db.session.commit()


def _file_names(response: dict) -> list[str]:
    return [item["relative_path"] for item in response["files"]]


def test_list_shared_returns_only_granted_indexed_files(serving_context):
    app, service, requester, media_dir, root = serving_context
    with app.app_context():
        _index_file(root / "trip" / "a.jpg", b"shared")
        _index_file(root / "private" / "secret.jpg", b"private")
        p2p_repository.create_grant(
            db.session,
            requester.device_id,
            GRANT_SCOPE_FOLDER,
            media_dir_id=media_dir.id,
            relative_path="trip",
        )

    def fail_if_hashing_during_browse(path):
        raise AssertionError(f"browse should not hash {path}")

    service._sha256_file = fail_if_hashing_during_browse
    response = service._handle_stream_request(build_list_shared_request(requester))

    assert response["status"] == "ok"
    assert _file_names(response) == ["trip/a.jpg"]
    manifest = response["files"][0]
    assert manifest["media_dir_id"] == media_dir.id
    assert manifest["size"] == len(b"shared")
    assert "sha256" not in manifest
    assert str(root) not in json.dumps(response)


def test_pull_file_returns_requested_chunk(serving_context):
    app, service, requester, media_dir, root = serving_context
    with app.app_context():
        _index_file(root / "trip" / "a.jpg", b"abcdef")
        p2p_repository.create_grant(
            db.session,
            requester.device_id,
            GRANT_SCOPE_MEDIA_DIR,
            media_dir_id=media_dir.id,
        )

    request = build_pull_file_request(requester, media_dir.id, "trip/a.jpg", offset=2, length=3)
    response = service._handle_stream_request(request)

    assert response["status"] == "ok"
    assert response["offset"] == 2
    assert response["next_offset"] == 5
    assert response["size"] == 6
    assert response["eof"] is False
    assert base64.b64decode(response["data_b64"]) == b"cde"
    assert response["chunk_sha256"] == hashlib.sha256(b"cde").hexdigest()


def test_pull_file_denies_out_of_scope_file(serving_context):
    app, service, requester, media_dir, root = serving_context
    with app.app_context():
        _index_file(root / "trip" / "a.jpg", b"shared")
        _index_file(root / "private" / "secret.jpg", b"private")
        p2p_repository.create_grant(
            db.session,
            requester.device_id,
            GRANT_SCOPE_FOLDER,
            media_dir_id=media_dir.id,
            relative_path="trip",
        )

    request = build_pull_file_request(requester, media_dir.id, "private/secret.jpg", offset=0, length=10)
    response = service._handle_stream_request(request)

    assert response["status"] == "error"
    assert "no active share grant" in response["detail"]


def test_revoked_grant_stops_next_pull(serving_context):
    app, service, requester, media_dir, root = serving_context
    with app.app_context():
        _index_file(root / "trip" / "a.jpg", b"shared")
        grant = p2p_repository.create_grant(
            db.session,
            requester.device_id,
            GRANT_SCOPE_MEDIA_DIR,
            media_dir_id=media_dir.id,
        )
        p2p_repository.revoke_grant(db.session, grant.id)

    request = build_pull_file_request(requester, media_dir.id, "trip/a.jpg", offset=0, length=10)
    response = service._handle_stream_request(request)

    assert response["status"] == "error"
    assert "no active share grant" in response["detail"]


def test_revoked_device_cannot_list_shared(serving_context):
    app, service, requester, _media_dir, _root = serving_context
    with app.app_context():
        p2p_repository.mark_device_revoked(db.session, requester.device_id)

    response = service._handle_stream_request(build_list_shared_request(requester))

    assert response["status"] == "error"
    assert "not a trusted device" in response["detail"]


def test_pull_file_rejects_traversal_even_when_signed(serving_context):
    _app, service, requester, media_dir, _root = serving_context
    request = build_pull_file_request(requester, media_dir.id, "../secret.jpg", offset=0, length=10)
    response = service._handle_stream_request(request)

    assert response["status"] == "error"
    assert "inside the media directory" in response["detail"]


def test_pull_file_resumes_partial_and_verifies_complete_file(serving_context):
    _app, service, requester, _media_dir, root = serving_context
    remote_content = b"abcdef"
    expected_sha256 = hashlib.sha256(remote_content).hexdigest()
    partial = root / "Shared" / requester.device_id / "trip" / "a.jpg.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"abc")
    calls = []

    def fake_pull_file_chunk(peer_device_id, media_dir_id, relative_path, offset=0, length=10):
        calls.append((peer_device_id, media_dir_id, relative_path, offset, length))
        data = remote_content[offset:offset + length]
        next_offset = offset + len(data)
        return {
            "media_dir_id": media_dir_id,
            "relative_path": relative_path,
            "offset": offset,
            "next_offset": next_offset,
            "size": len(remote_content),
            "eof": next_offset >= len(remote_content),
            "bytes": len(data),
            "chunk_sha256": hashlib.sha256(data).hexdigest(),
            "data_b64": base64.b64encode(data).decode("ascii"),
        }

    service.pull_file_chunk = fake_pull_file_chunk

    result = service.pull_file(
        requester.device_id,
        "remote-lib",
        "trip/a.jpg",
        root,
        expected_sha256=expected_sha256,
        chunk_size=3,
    )

    destination = root / "Shared" / requester.device_id / "trip" / "a.jpg"
    assert calls == [(requester.device_id, "remote-lib", "trip/a.jpg", 3, 3)]
    assert destination.read_bytes() == remote_content
    assert not partial.exists()
    assert result["relative_path"] == f"Shared/{requester.device_id}/trip/a.jpg"
    assert result["sha256"] == expected_sha256
