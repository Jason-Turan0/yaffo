from __future__ import annotations

import secrets

from flask import Flask, Response, jsonify, render_template, request, session
from flask_babel import gettext

CSRF_CODE = "csrf_failed"
_CSRF_SESSION_KEY = "_yaffo_csrf_token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def csrf_token() -> str:
    token = session.get(_CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_CSRF_SESSION_KEY] = token
    return token


def csrf_is_valid() -> bool:
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    expected = session.get(_CSRF_SESSION_KEY)
    return bool(supplied and expected and secrets.compare_digest(supplied, expected))


def request_expects_json() -> bool:
    return (
        request.path.startswith("/api/")
        or request.headers.get("HX-Request") == "true"
        or request.headers.get("X-Yaffo-Response") == "json"
        or request.accept_mimetypes.best == "application/json"
    )


def _csrf_failure() -> Response:
    message = gettext("This request could not be verified. Refresh the page and try again.")
    if request_expects_json():
        response = jsonify({"error": message, "code": CSRF_CODE})
        response.status_code = 403
    else:
        response = Response(
            render_template("security/csrf_failed.html", message=message),
            status=403,
            content_type="text/html; charset=utf-8",
        )
    response.headers["Cache-Control"] = "no-store"
    return response


def init_request_security(app: Flask) -> None:
    app.config.setdefault("CSRF_ENABLED", True)

    @app.before_request
    def protect_unsafe_request() -> Response | None:
        if (
            request.method in _SAFE_METHODS
            or not app.config["CSRF_ENABLED"]
            or app.config.get("TESTING")
            or app.config.get("DEMO_MODE")
        ):
            return None
        if csrf_is_valid():
            return None
        return _csrf_failure()

    @app.context_processor
    def inject_request_security() -> dict[str, str]:
        return {"csrf_token": csrf_token()}
