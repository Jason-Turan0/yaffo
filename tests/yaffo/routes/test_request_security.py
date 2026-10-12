from __future__ import annotations

import pytest

from yaffo.app import create_app
from yaffo.db import db

pytestmark = pytest.mark.unit


def test_production_unsafe_requests_require_csrf_token(tmp_path):
    app = create_app(
        db_path=tmp_path / "csrf.db",
        config={
            "SECRET_KEY": "csrf-test-secret",
            "TESTING": False,
            "CSRF_ENABLED": True,
        },
    )
    with app.app_context():
        db.create_all()

    client = app.test_client()
    assert client.get("/").status_code == 200

    blocked = client.post(
        "/settings/locale",
        data={"locale": "de"},
        headers={"X-Yaffo-Response": "json"},
    )
    assert blocked.status_code == 403
    assert blocked.get_json()["code"] == "csrf_failed"
    assert blocked.headers["Cache-Control"] == "no-store"

    with client.session_transaction() as browser_session:
        token = browser_session["_yaffo_csrf_token"]
    allowed = client.post(
        "/settings/locale",
        data={"locale": "de", "csrf_token": token},
    )
    assert allowed.status_code == 302


@pytest.fixture
def guarded_client(tmp_path):
    app = create_app(
        db_path=tmp_path / "guard.db",
        config={"TESTING": True, "WEB_ALLOWED_HOSTS": ["yaffo.lan"]},
    )
    with app.app_context():
        db.create_all()
    return app.test_client()


def test_unknown_host_is_refused(guarded_client):
    response = guarded_client.get("/", headers={"Host": "evil.example:5001"})
    assert response.status_code == 400


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1:5001", "[::1]:5001", "yaffo.lan:8000"])
def test_loopback_and_configured_hosts_are_served(guarded_client, host):
    assert guarded_client.get("/", headers={"Host": host}).status_code == 200


def test_cross_site_post_is_refused(guarded_client):
    response = guarded_client.post(
        "/settings/locale",
        data={"locale": "de"},
        headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_same_site_post_from_another_port_is_refused(guarded_client):
    response = guarded_client.post(
        "/settings/locale",
        data={"locale": "de"},
        headers={"Host": "127.0.0.1:5002", "Sec-Fetch-Site": "same-site", "Origin": "http://127.0.0.1:5003"},
    )
    assert response.status_code == 403
    response = guarded_client.post(
        "/settings/locale",
        data={"locale": "de"},
        headers={"Host": "127.0.0.1:5002", "Origin": "http://127.0.0.1:5003"},
        environ_overrides={"wsgi.url_scheme": "http"},
    )
    assert response.status_code == 403


def test_cross_origin_refusal_is_json_for_scripts(guarded_client):
    response = guarded_client.post(
        "/settings/locale",
        data={"locale": "de"},
        headers={"Origin": "https://evil.example", "X-Yaffo-Response": "json"},
    )
    assert response.status_code == 403
    assert response.get_json()["code"] == "cross_origin_request"


def test_same_origin_post_passes(guarded_client):
    response = guarded_client.post(
        "/settings/locale",
        data={"locale": "de"},
        headers={"Host": "127.0.0.1:5002", "Sec-Fetch-Site": "same-origin", "Origin": "http://127.0.0.1:5002"},
    )
    assert response.status_code == 302


def test_headerless_post_passes(guarded_client):
    assert guarded_client.post("/settings/locale", data={"locale": "de"}).status_code == 302


def test_cross_site_get_is_served(guarded_client):
    assert guarded_client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
