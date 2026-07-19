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
