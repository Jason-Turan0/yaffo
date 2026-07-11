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
from yaffo.p2p.messages import (
    build_list_files_request,
    build_list_shared_request,
    build_pull_file_request,
    build_pull_preview_request,
)
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


def _index_file(path, content: bytes, **columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    db.session.add(MediaItem(full_file_path=str(path), media_type=MEDIA_TYPE_PHOTO, **columns))
    db.session.commit()


def _file_names(response: dict) -> list[str]:
    return [item["relative_path"] for item in response["files"]]


def test_list_shared_returns_scopes_with_counts_not_files(serving_context):
    app, service, requester, media_dir, root = serving_context
    with app.app_context():
        _index_file(root / "trip" / "a.jpg", b"shared")
        _index_file(root / "trip" / "b.jpg", b"shared too")
        _index_file(root / "private" / "secret.jpg", b"private")
        p2p_repository.create_grant(
            db.session,
            requester.device_id,
            GRANT_SCOPE_FOLDER,
            media_dir_id=media_dir.id,
            relative_path="trip",
        )

    response = service._handle_stream_request(build_list_shared_request(requester))

    assert response["status"] == "ok"
    assert "files" not in response
    assert response["scopes"] == [
        {
            "scope_type": GRANT_SCOPE_FOLDER,
            "media_dir_id": media_dir.id,
            "relative_path": "trip",
            "name": root.name,
            "file_count": 2,
        }
    ]
    assert str(root) not in json.dumps(response)


def test_list_files_pages_search_and_manifests(serving_context):
    app, service, requester, media_dir, root = serving_context
    with app.app_context():
        for name in ("trip/a.jpg", "trip/b.jpg", "trip/c.png", "private/secret.jpg"):
            _index_file(root / name, name.encode())
        p2p_repository.create_grant(
            db.session,
            requester.device_id,
            GRANT_SCOPE_MEDIA_DIR,
            media_dir_id=media_dir.id,
        )

    def fail_if_hashing_during_browse(path):
        raise AssertionError(f"browse should not hash {path}")

    service._sha256_file = fail_if_hashing_during_browse

    page_one = service._handle_stream_request(
        build_list_files_request(requester, media_dir.id, "trip", filters={}, offset=0, limit=2)
    )
    assert page_one["status"] == "ok"
    assert page_one["total"] == 3
    assert _file_names(page_one) == ["trip/a.jpg", "trip/b.jpg"]
    manifest = page_one["files"][0]
    assert manifest["media_dir_id"] == media_dir.id
    assert manifest["size"] == len(b"trip/a.jpg")
    assert "sha256" not in manifest
    assert str(root) not in json.dumps(page_one)

    page_two = service._handle_stream_request(
        build_list_files_request(requester, media_dir.id, "trip", filters={}, offset=2, limit=2)
    )
    assert _file_names(page_two) == ["trip/c.png"]

    searched = service._handle_stream_request(
        build_list_files_request(requester, media_dir.id, "", filters={"path": "b.jpg"}, offset=0, limit=10)
    )
    assert searched["total"] == 1
    assert _file_names(searched) == ["trip/b.jpg"]


def test_list_files_applies_filters_after_grant_and_returns_facets(serving_context):
    app, service, requester, media_dir, root = serving_context
    with app.app_context():
        _index_file(root / "trip" / "beach.jpg", b"1", year=2023, month=7, device="Pixel",
                    date_taken="2023-07-01T10:00:00")
        _index_file(root / "trip" / "hike.jpg", b"2", year=2024, month=3, device="X-T200",
                    date_taken="2024-03-05T10:00:00")
        _index_file(root / "private" / "secret.jpg", b"3", year=2021, device="Secret-Cam")
        p2p_repository.create_grant(
            db.session,
            requester.device_id,
            GRANT_SCOPE_FOLDER,
            media_dir_id=media_dir.id,
            relative_path="trip",
        )

    unfiltered = service._handle_stream_request(
        build_list_files_request(requester, media_dir.id, "trip", filters={}, offset=0, limit=10)
    )
    # Newest first, and the facets only reflect the granted scope — the
    # private folder's year/device must not leak.
    assert _file_names(unfiltered) == ["trip/hike.jpg", "trip/beach.jpg"]
    assert unfiltered["facets"]["years"] == [2023, 2024]
    assert unfiltered["facets"]["devices"] == ["Pixel", "X-T200"]
    assert unfiltered["files"][0]["date_taken"] == "2024-03-05T10:00:00"

    by_year = service._handle_stream_request(
        build_list_files_request(requester, media_dir.id, "trip", filters={"year": 2023}, offset=0, limit=10)
    )
    assert _file_names(by_year) == ["trip/beach.jpg"]

    by_device = service._handle_stream_request(
        build_list_files_request(requester, media_dir.id, "trip", filters={"device": "X-T200"}, offset=0, limit=10)
    )
    assert _file_names(by_device) == ["trip/hike.jpg"]

    # A filter can never widen results beyond the grant, and unknown filter
    # keys are rejected rather than silently ignored.
    outside = service._handle_stream_request(
        build_list_files_request(requester, media_dir.id, "trip", filters={"path": "secret"}, offset=0, limit=10)
    )
    assert outside["total"] == 0
    unknown = service._handle_stream_request(
        build_list_files_request(requester, media_dir.id, "trip", filters={"person": 1}, offset=0, limit=10)
    )
    assert unknown["status"] == "error"
    assert "unknown filters" in unknown["detail"]


def test_list_files_entity_filters_and_facets(serving_context):
    """People/labels/tags/locations: the peer filters by the SERVING side's
    entity ids/values, learned from the facets — and the facets are computed
    strictly within the granted scope."""
    from yaffo.db.models import ClassificationLabel, Face, MediaLabel, Person, PersonFace, Tag

    app, service, requester, media_dir, root = serving_context
    with app.app_context():
        _index_file(root / "trip" / "beach.jpg", b"1", location_name="Lisbon",
                    latitude=38.72, longitude=-9.14)
        _index_file(root / "trip" / "hike.jpg", b"2")
        _index_file(root / "private" / "secret.jpg", b"3", location_name="Hideout")
        beach = db.session.query(MediaItem).filter(MediaItem.full_file_path.contains("beach")).one()
        secret = db.session.query(MediaItem).filter(MediaItem.full_file_path.contains("secret")).one()

        alice = Person(name="Alice")
        bob = Person(name="Bob")  # only on the private photo — must not leak
        label = ClassificationLabel(name="beach", prompt="a beach")
        db.session.add_all([alice, bob, label])
        db.session.flush()
        face_a = Face(media_item_id=beach.id)
        face_b = Face(media_item_id=secret.id)
        db.session.add_all([face_a, face_b])
        db.session.flush()
        db.session.add_all(
            [
                PersonFace(person_id=alice.id, face_id=face_a.id),
                PersonFace(person_id=bob.id, face_id=face_b.id),
                MediaLabel(media_item_id=beach.id, label_id=label.id),
                Tag(media_item_id=beach.id, tag_name="event", tag_value="holiday"),
                Tag(media_item_id=secret.id, tag_name="secret-tag", tag_value="shh"),
            ]
        )
        db.session.commit()
        alice_id, label_id = alice.id, label.id
        p2p_repository.create_grant(
            db.session,
            requester.device_id,
            GRANT_SCOPE_FOLDER,
            media_dir_id=media_dir.id,
            relative_path="trip",
        )

    listed = service._handle_stream_request(
        build_list_files_request(requester, media_dir.id, "trip", filters={"tag_name": "event"}, offset=0, limit=10)
    )
    facets = listed["facets"]
    assert facets["people"] == [{"id": alice_id, "name": "Alice"}]  # no Bob
    assert facets["labels"] == [{"id": label_id, "name": "beach"}]
    assert facets["tag_names"] == ["event"]  # no secret-tag
    assert facets["locations"] == ["Lisbon"]  # no Hideout
    assert facets["tag_values"] == ["holiday"]
    assert _file_names(listed) == ["trip/beach.jpg"]  # the tag_name filter applied

    by_person = service._handle_stream_request(
        build_list_files_request(requester, media_dir.id, "trip", filters={"people": [alice_id]}, offset=0, limit=10)
    )
    assert _file_names(by_person) == ["trip/beach.jpg"]

    by_label = service._handle_stream_request(
        build_list_files_request(requester, media_dir.id, "trip", filters={"labels": [label_id]}, offset=0, limit=10)
    )
    assert _file_names(by_label) == ["trip/beach.jpg"]

    by_location = service._handle_stream_request(
        build_list_files_request(requester, media_dir.id, "trip", filters={"locations": ["Lisbon"]}, offset=0, limit=10)
    )
    assert _file_names(by_location) == ["trip/beach.jpg"]

    near_lisbon = service._handle_stream_request(
        build_list_files_request(
            requester, media_dir.id, "trip",
            filters={"proximity_lat": 38.7, "proximity_lon": -9.1, "proximity_km": 25.0},
            offset=0, limit=10,
        )
    )
    assert _file_names(near_lisbon) == ["trip/beach.jpg"]

    bad_type = service._handle_stream_request(
        build_list_files_request(requester, media_dir.id, "trip", filters={"people": ["alice"]}, offset=0, limit=10)
    )
    assert bad_type["status"] == "error"
    assert "invalid value for filter 'people'" in bad_type["detail"]


def test_list_files_denies_scope_outside_grant(serving_context):
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

    for scope in ("private", ""):  # a sibling folder, and the whole media dir
        response = service._handle_stream_request(
            build_list_files_request(requester, media_dir.id, scope, filters={}, offset=0, limit=10)
        )
        assert response["status"] == "error"
        assert "no active share grant" in response["detail"]

    inside = service._handle_stream_request(
        build_list_files_request(requester, media_dir.id, "trip", filters={}, offset=0, limit=10)
    )
    assert inside["status"] == "ok"
    assert _file_names(inside) == ["trip/a.jpg"]


def _write_jpeg(path, width: int, height: int):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), color=(200, 60, 60)).save(path, format="JPEG")
    db.session.add(MediaItem(full_file_path=str(path), media_type=MEDIA_TYPE_PHOTO))
    db.session.commit()


def test_pull_preview_returns_downscaled_jpeg(serving_context):
    import io

    from PIL import Image

    app, service, requester, media_dir, root = serving_context
    with app.app_context():
        _write_jpeg(root / "trip" / "big.jpg", 1600, 900)
        p2p_repository.create_grant(
            db.session,
            requester.device_id,
            GRANT_SCOPE_MEDIA_DIR,
            media_dir_id=media_dir.id,
        )

    response = service._handle_stream_request(
        build_pull_preview_request(requester, media_dir.id, "trip/big.jpg", max_dimension=256)
    )

    assert response["status"] == "ok"
    preview = Image.open(io.BytesIO(base64.b64decode(response["data_b64"])))
    assert preview.format == "JPEG"
    assert max(preview.size) <= 256
    assert str(root) not in json.dumps(response)


def test_pull_preview_denies_out_of_scope_file(serving_context):
    app, service, requester, media_dir, root = serving_context
    with app.app_context():
        _write_jpeg(root / "private" / "secret.jpg", 100, 100)
        p2p_repository.create_grant(
            db.session,
            requester.device_id,
            GRANT_SCOPE_FOLDER,
            media_dir_id=media_dir.id,
            relative_path="trip",
        )

    response = service._handle_stream_request(
        build_pull_preview_request(requester, media_dir.id, "private/secret.jpg", max_dimension=256)
    )

    assert response["status"] == "error"
    assert "no active share grant" in response["detail"]


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
    partial = root / requester.device_id / "trip" / "a.jpg.partial"
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
        destination_collection_path="trip",
        source_scope_path="trip",
    )

    destination = root / requester.device_id / "trip" / "a.jpg"
    assert calls == [(requester.device_id, "remote-lib", "trip/a.jpg", 3, 3)]
    assert destination.read_bytes() == remote_content
    assert not partial.exists()
    assert result["relative_path"] == f"{requester.device_id}/trip/a.jpg"
    assert result["sha256"] == expected_sha256
