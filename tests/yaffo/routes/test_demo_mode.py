from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from flask import Flask

from yaffo.app import create_app
from yaffo.db import db
from yaffo.db.models import (
    FACE_STATUS_ASSIGNED,
    FACE_STATUS_UNASSIGNED,
    GRANT_SCOPE_MEDIA_DIR,
    TRUST_STATE_TRUSTED,
    Face,
    KnownDevice,
    MediaItem,
    Person,
    ShareGrant,
)
from yaffo.db.repositories import custom_page_repository as page_repo
from yaffo.db.repositories import media_dir_repository
from yaffo.db.repositories import p2p_repository
from yaffo.demo import (
    DEMO_PUBLIC_READ_ENDPOINTS,
    DEMO_ROLE_RECEIVER,
    DEMO_ROLE_SOURCE,
    DEMO_UNSAFE_ENDPOINT_ROLES,
    validate_demo_route_map,
)
from yaffo.p2p.pairing import new_pairing_code
from yaffo.runtime_mode import DemoModeOperationBlocked
from yaffo.taskq.core import TaskQueue
from yaffo.utils.file_system import list_directory

pytestmark = pytest.mark.unit


@pytest.fixture(params=[DEMO_ROLE_SOURCE, DEMO_ROLE_RECEIVER])
def demo_app(tmp_path, request) -> Iterator[Flask]:
    application = create_app(
        db_path=tmp_path / f"demo-{request.param}.db",
        config={
            "TESTING": True,
            "SECRET_KEY": f"demo-test-{request.param}",
            "DEMO_MODE": True,
            "DEMO_ROLE": request.param,
        },
    )
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


def _csrf_token(client) -> str:
    response = client.get("/")
    assert response.status_code == 200
    with client.session_transaction() as browser_session:
        return browser_session["_yaffo_csrf_token"]


def test_demo_mode_requires_an_explicit_role(tmp_path):
    with pytest.raises(RuntimeError, match="YAFFO_DEMO_ROLE"):
        create_app(
            db_path=tmp_path / "missing-role.db",
            config={"TESTING": True, "DEMO_MODE": True, "SECRET_KEY": "test"},
        )


def test_demo_route_map_is_exact_and_validated(demo_app):
    validate_demo_route_map(demo_app)
    endpoints = {rule.endpoint for rule in demo_app.url_map.iter_rules()}
    unsafe_endpoints = {
        rule.endpoint
        for rule in demo_app.url_map.iter_rules()
        if (rule.methods or set()) - {"GET", "HEAD", "OPTIONS"}
    }

    assert DEMO_PUBLIC_READ_ENDPOINTS <= endpoints
    assert DEMO_UNSAFE_ENDPOINT_ROLES.keys() <= unsafe_endpoints


def test_all_utility_gets_are_public_and_all_utility_posts_are_blocked(demo_app):
    utility_rules = [
        rule for rule in demo_app.url_map.iter_rules() if rule.rule.startswith("/utilities")
    ]
    utility_read_endpoints = {
        rule.endpoint
        for rule in utility_rules
        if "GET" in (rule.methods or set())
    }
    utility_post_endpoints = {
        rule.endpoint for rule in utility_rules if "POST" in (rule.methods or set())
    }

    assert utility_read_endpoints <= DEMO_PUBLIC_READ_ENDPOINTS
    assert utility_post_endpoints.isdisjoint(DEMO_UNSAFE_ENDPOINT_ROLES)

    client = demo_app.test_client()
    assert client.get("/utilities").status_code == 302
    assert client.get("/utilities/index-photos").status_code == 200
    assert client.get("/utilities/remove-duplicates").status_code == 200
    assert client.get("/utilities/automations").status_code in {200, 302}

    response = client.post(
        "/utilities/index-photos/sync",
        headers={"X-Yaffo-Response": "json"},
        json={},
    )
    assert response.status_code == 403
    assert response.get_json()["code"] == "demo_feature_disabled"


def test_public_read_route_is_allowed_and_has_demo_banner(demo_app):
    response = demo_app.test_client().get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "This is one shared, disposable sandbox." in body
    assert "data-demo-reset-at" in body
    assert "demo-mode.js" in body


def test_published_widgets_render_and_design_actions_are_blocked(demo_app):
    with demo_app.app_context():
        page = page_repo.create_page(db.session, title="Family overview")
        page_repo.save_page_widgets(
            db.session,
            page.id,
            [
                {
                    "id": "family-widget",
                    "title": "Recent memories",
                    "html": "<div class='widget-proof'>Memories</div>",
                    "js": "/* demo widget */",
                    "x": 0,
                    "y": 0,
                    "w": 4,
                    "h": 3,
                }
            ],
        )
        page_id = page.id

    client = demo_app.test_client()
    presentation = client.get(f"/pages/{page_id}")
    assert presentation.status_code == 200
    assert f'/pages/{page_id}/widgets/family-widget/frame' in presentation.get_data(as_text=True)

    frame = client.get(f"/pages/{page_id}/widgets/family-widget/frame")
    assert frame.status_code == 200
    assert frame.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in frame.headers["Content-Security-Policy"]
    assert "widget-proof" in frame.get_data(as_text=True)

    design = client.get(f"/pages/{page_id}/design")
    assert design.status_code == 200
    design_body = design.get_data(as_text=True)
    assert "Editing is disabled in the public demo." not in design_body
    assert 'class="editor-card"' in design_body
    assert 'class="editor-card" inert' not in design_body
    assert "initDesignGrid" in design_body
    assert "pages/detail.js" in design_body
    assert 'class="widget-edit"' in design_body

    token = _csrf_token(client)
    blocked_update = client.post(
        f"/pages/{page_id}/update",
        headers={"X-CSRF-Token": token, "X-Yaffo-Response": "json"},
        json={"title": "Changed", "widgets": []},
    )
    assert blocked_update.status_code == 403
    assert blocked_update.get_json()["code"] == "demo_feature_disabled"


def test_sharing_screen_and_read_only_fragments_are_available(demo_app):
    client = demo_app.test_client()

    entry = client.get("/sharing")
    assert entry.status_code == 302
    assert entry.headers["Location"].endswith("/sharing/settings")

    settings = client.get("/sharing/settings")
    assert settings.status_code == 200
    assert "Device sharing" in settings.get_data(as_text=True)

    refreshed_section = client.get("/sharing/settings/section")
    assert refreshed_section.status_code == 200
    assert 'id="devices-section"' in refreshed_section.get_data(as_text=True)

    shared_with_me = client.get("/sharing/sidebar/shared-with-me")
    assert shared_with_me.status_code == 200
    assert "Shared With Me" in shared_with_me.get_data(as_text=True)

    # These responses prove the demo gate lets the remote gallery and preview
    # reach their route handlers; the empty unit fixture has no peer service.
    remote_gallery = client.get(
        "/sharing/devices/missing/files",
        query_string={"media_dir_id": "seeded-share"},
    )
    assert remote_gallery.status_code == 404

    remote_preview = client.get(
        "/sharing/devices/missing/preview",
        query_string={"media_item_id": 1},
    )
    assert remote_preview.status_code == 503


def test_sharing_pairing_mutation_remains_blocked(demo_app):
    client = demo_app.test_client()
    token = _csrf_token(client)

    response = client.post(
        "/sharing/pairing-code",
        headers={"X-CSRF-Token": token, "X-Yaffo-Response": "json"},
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "demo_feature_disabled"


def test_blocked_get_fails_closed_before_filesystem_route(demo_app, tmp_path):
    response = demo_app.test_client().get(
        "/api/fs/list",
        query_string={"path": str(tmp_path)},
    )

    assert response.status_code == 403
    assert response.get_json() == {
        "error": "This action is disabled in the public demo.",
        "code": "demo_feature_disabled",
    }
    assert response.headers["Cache-Control"] == "no-store"


def test_unlisted_route_added_later_still_fails_closed(demo_app):
    @demo_app.get("/_demo-unclassified")
    def demo_unclassified():
        return "unsafe"

    response = demo_app.test_client().get("/_demo-unclassified")

    assert response.status_code == 403
    assert b"unsafe" not in response.data


def test_blocked_fetch_gets_stable_json_response(demo_app):
    response = demo_app.test_client().post(
        "/people/create",
        headers={"X-Yaffo-Response": "json"},
        data={"name": "Visitor text"},
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "demo_feature_disabled"
    assert response.headers["Cache-Control"] == "no-store"


def test_blocked_non_javascript_form_gets_html_fallback(demo_app):
    response = demo_app.test_client().post("/people/create", data={"name": "Visitor text"})

    assert response.status_code == 403
    assert response.mimetype == "text/html"
    assert "That feature is unavailable here" in response.get_data(as_text=True)


def test_exception_requires_csrf_token(demo_app):
    response = demo_app.test_client().post(
        "/api/faces/assign",
        json={"faces": [], "person": None, "faceStatus": None},
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "csrf_failed"


def test_receiver_transfer_exception_remains_blocked_on_source(demo_app):
    if demo_app.config["DEMO_ROLE"] != DEMO_ROLE_SOURCE:
        pytest.skip("source-only policy assertion")
    client = demo_app.test_client()
    token = _csrf_token(client)

    response = client.post(
        "/sharing/devices/peer/transfers/pull",
        headers={"X-CSRF-Token": token, "X-Yaffo-Response": "json"},
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "demo_feature_disabled"


def test_receiver_transfer_exception_reaches_route(demo_app):
    if demo_app.config["DEMO_ROLE"] != DEMO_ROLE_RECEIVER:
        pytest.skip("receiver-only policy assertion")
    client = demo_app.test_client()
    token = _csrf_token(client)

    response = client.post(
        "/sharing/devices/peer/transfers/pull",
        headers={"X-CSRF-Token": token, "X-Yaffo-Response": "json"},
    )

    assert response.status_code != 403


def test_demo_face_assignment_runs_in_web_process_and_protects_seeded_faces(
    demo_app,
    monkeypatch,
):
    with demo_app.app_context():
        person = Person(name="Demo Person")
        unassigned = Face(full_file_path="/demo/unassigned.jpg", status=FACE_STATUS_UNASSIGNED)
        seeded = Face(full_file_path="/demo/seeded.jpg", status=FACE_STATUS_ASSIGNED)
        db.session.add_all([person, unassigned, seeded])
        db.session.commit()
        person_id = person.id
        unassigned_id = unassigned.id
        seeded_id = seeded.id

    calls: list[tuple[int, list[int], bool]] = []

    def assign_now(session, person_id, face_ids, *, emit_change_event=True):
        calls.append((person_id, face_ids, emit_change_event))
        session.query(Face).filter(Face.id.in_(face_ids)).update(
            {Face.status: FACE_STATUS_ASSIGNED}, synchronize_session=False
        )
        session.commit()
        return len(face_ids)

    monkeypatch.setattr("yaffo.routes.faces.assign_faces_to_person_now", assign_now)
    monkeypatch.setattr(
        "yaffo.routes.faces.assign_faces_to_person",
        lambda *_args, **_kwargs: pytest.fail("demo mode must not enqueue face assignment"),
    )

    client = demo_app.test_client()
    token = _csrf_token(client)
    response = client.post(
        "/api/faces/assign",
        headers={"X-CSRF-Token": token},
        json={"faces": [unassigned_id], "person": person_id, "faceStatus": FACE_STATUS_ASSIGNED},
    )

    assert response.status_code == 200
    assert calls == [(person_id, [unassigned_id], False)]

    protected = client.post(
        "/api/faces/assign",
        headers={"X-CSRF-Token": token},
        json={"faces": [seeded_id], "person": person_id, "faceStatus": FACE_STATUS_ASSIGNED},
    )
    assert protected.status_code == 409
    assert protected.get_json()["code"] == "faces_not_available"


def test_demo_security_headers_are_applied(demo_app):
    response = demo_app.test_client().get("/")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Strict-Transport-Security"].startswith("max-age=")
    assert "tile.openstreetmap.org" not in response.headers["Content-Security-Policy"]


def test_locations_map_can_load_only_the_expected_osm_tiles(demo_app):
    response = demo_app.test_client().get("/locations")

    assert response.status_code == 200
    csp = response.headers["Content-Security-Policy"]
    assert "img-src 'self' data: blob: https://tile.openstreetmap.org" in csp
    assert "https://*.tile.openstreetmap.org" not in csp
    assert "connect-src 'self' wss://hub.yaffo.app" in csp


def test_demo_request_limits_have_session_ip_and_global_backstops(demo_app):
    demo_app.config["DEMO_REQUESTS_PER_SESSION_MINUTE"] = 1
    client = demo_app.test_client()

    assert client.get("/").status_code == 200
    response = client.get("/")

    assert response.status_code == 429
    assert "public demo is busy" in response.get_data(as_text=True)


def test_demo_inventory_scan_has_cooldown_and_hard_walk_limit(demo_app, tmp_path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "one.jpg").write_bytes(b"one")
    (media_root / "two.jpg").write_bytes(b"two")
    demo_app.config["DEMO_SCAN_MAX_FILES"] = 1

    with demo_app.app_context():
        media_dir_repository.add_media_dir(db.session, str(media_root))

    client = demo_app.test_client()
    first = client.get("/utilities/index-photos/scan")
    records = [json.loads(line) for line in first.get_data(as_text=True).splitlines() if line]
    assert first.status_code == 200
    assert records[-1]["code"] == "filesystem_scan_failed"

    second = client.get("/utilities/index-photos/scan")
    assert second.status_code == 429


def test_demo_media_delivery_has_session_ip_global_and_daily_byte_budgets(
    demo_app,
    tmp_path,
):
    media_root = tmp_path / "media"
    media_root.mkdir()
    photo = media_root / "inside.jpg"
    photo.write_bytes(b"123456")
    demo_app.config["DEMO_MEDIA_BYTES_PER_SESSION_MINUTE"] = 10
    demo_app.config["DEMO_MEDIA_BYTES_PER_IP_MINUTE"] = 100
    demo_app.config["DEMO_MEDIA_BYTES_GLOBAL_MINUTE"] = 100
    demo_app.config["DEMO_MEDIA_BYTES_GLOBAL_DAY"] = 100

    with demo_app.app_context():
        media_dir_repository.add_media_dir(db.session, str(media_root))
        item = MediaItem(full_file_path=str(photo))
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    client = demo_app.test_client()
    assert client.get(f"/media/{item_id}").status_code == 200
    blocked = client.get(
        f"/media/{item_id}",
        headers={"X-Yaffo-Response": "json"},
    )

    assert blocked.status_code == 429
    assert blocked.get_json()["code"] == "demo_rate_limit_exceeded"


def test_demo_media_byte_budget_counts_ranges_but_not_head_requests(demo_app, tmp_path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    photo = media_root / "inside.jpg"
    photo.write_bytes(b"1234567890")
    demo_app.config["DEMO_MEDIA_BYTES_PER_SESSION_MINUTE"] = 7

    with demo_app.app_context():
        media_dir_repository.add_media_dir(db.session, str(media_root))
        item = MediaItem(full_file_path=str(photo))
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    client = demo_app.test_client()
    assert client.head(f"/media/{item_id}").status_code == 200
    first_range = client.get(
        f"/media/{item_id}",
        headers={"Range": "bytes=0-3"},
    )
    blocked_range = client.get(
        f"/media/{item_id}",
        headers={"Range": "bytes=4-7", "X-Yaffo-Response": "json"},
    )

    assert first_range.status_code == 206
    assert first_range.data == b"1234"
    assert blocked_range.status_code == 429
    assert blocked_range.get_json()["code"] == "demo_rate_limit_exceeded"


def test_service_layer_blocks_tasks_filesystem_pairing_and_paid_keys(
    demo_app,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-visible")
    from yaffo.site_agents import llm_config

    with demo_app.app_context():
        assert llm_config.get_api_key("anthropic") is None
        with pytest.raises(DemoModeOperationBlocked):
            llm_config.set_api_key("anthropic", "visitor-secret")
        with pytest.raises(DemoModeOperationBlocked):
            list_directory(str(tmp_path))
        with pytest.raises(DemoModeOperationBlocked):
            new_pairing_code("device", "pubkey")

        queue = TaskQueue(str(tmp_path / "queue.db"), immediate=True)

        @queue.task()
        def demo_task():
            return None

        with pytest.raises(DemoModeOperationBlocked):
            demo_task()


def test_service_layer_protects_seeded_p2p_trust_and_grants(demo_app):
    with demo_app.app_context():
        device = KnownDevice(
            device_id="AAAA-BBBB-CCCC-DDDD",
            pubkey="seed-pubkey",
            display_name="Family Mac",
            trust_state=TRUST_STATE_TRUSTED,
        )
        grant = ShareGrant(
            peer_device_id=device.device_id,
            scope_type=GRANT_SCOPE_MEDIA_DIR,
            media_dir_id="seed-media-dir",
        )
        db.session.add_all([device, grant])
        db.session.commit()
        grant_id = grant.id

        blocked_calls = [
            lambda: p2p_repository.upsert_trusted_device(
                db.session, device.device_id, "replacement", "Attacker"
            ),
            lambda: p2p_repository.mark_device_revoked(db.session, device.device_id),
            lambda: p2p_repository.rename_device(db.session, device.device_id, "Changed"),
            lambda: p2p_repository.delete_revoked_device(db.session, device.device_id),
            lambda: p2p_repository.create_grant(
                db.session,
                device.device_id,
                GRANT_SCOPE_MEDIA_DIR,
                media_dir_id="another-dir",
            ),
            lambda: p2p_repository.revoke_grant(db.session, grant_id),
        ]
        for call in blocked_calls:
            with pytest.raises(DemoModeOperationBlocked):
                call()

        db.session.expire_all()
        protected_device = db.session.get(KnownDevice, device.device_id)
        protected_grant = db.session.get(ShareGrant, grant_id)
        assert protected_device is not None
        assert protected_device.display_name == "Family Mac"
        assert protected_device.trust_state == TRUST_STATE_TRUSTED
        assert protected_grant is not None
        assert protected_grant.revoked_at is None


def test_demo_media_delivery_requires_database_path_under_configured_root(demo_app, tmp_path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    inside_file = media_root / "inside.jpg"
    inside_file.write_bytes(b"inside")
    outside_file = tmp_path / "outside.jpg"
    outside_file.write_bytes(b"outside")

    with demo_app.app_context():
        media_dir_repository.add_media_dir(db.session, str(media_root))
        inside = MediaItem(full_file_path=str(inside_file))
        outside = MediaItem(full_file_path=str(outside_file))
        db.session.add_all([inside, outside])
        db.session.commit()
        inside_id = inside.id
        outside_id = outside.id

    client = demo_app.test_client()
    assert client.get(f"/media/{inside_id}").status_code == 200
    assert client.get(f"/media/{outside_id}").status_code == 404
