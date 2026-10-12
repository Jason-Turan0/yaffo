from __future__ import annotations

import os
import secrets

from flask import Flask, Response, abort, jsonify, render_template, request, session
from flask_babel import gettext

from yaffo.config import get as get_config

CSRF_CODE = "csrf_failed"
CROSS_ORIGIN_CODE = "cross_origin_request"
_CSRF_SESSION_KEY = "_yaffo_csrf_token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_CROSS_ORIGIN_FETCH_SITES = frozenset({"cross-site", "same-site"})


def configured_allowed_hosts() -> list[str]:
    """Host names, beyond loopback, that a server bound past loopback answers to:
    `YAFFO_WEB_ALLOWED_HOSTS` (comma-separated) when set, else `web.allowed_hosts`."""
    env_value = os.environ.get("YAFFO_WEB_ALLOWED_HOSTS")
    if env_value is not None:
        names = env_value.split(",")
    else:
        configured = get_config("web", "allowed_hosts", [])
        names = configured if isinstance(configured, list) else []
    return [str(name).strip().lower() for name in names if str(name).strip()]


def request_hostname() -> str:
    """The `Host` header's name without its port. A DNS-rebinding page controls the
    name, not the port, so only the name is compared."""
    host = request.host.lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host


def _host_is_allowed(hostname: str, allowed: frozenset[str]) -> bool:
    if hostname in LOOPBACK_HOSTS or hostname in allowed:
        return True
    # Flask's TRUSTED_HOSTS form: ".example.com" matches example.com and subdomains.
    return any(
        name.startswith(".") and (hostname == name[1:] or hostname.endswith(name))
        for name in allowed
    )


def _is_cross_origin() -> bool:
    if request.headers.get("Sec-Fetch-Site", "").lower() in _CROSS_ORIGIN_FETCH_SITES:
        return True
    origin = request.headers.get("Origin")
    return origin is not None and origin.rstrip("/") != f"{request.scheme}://{request.host}"


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


def _cross_origin_failure() -> Response:
    message = gettext("This request could not be verified. Refresh the page and try again.")
    if request_expects_json():
        response = jsonify({"error": message, "code": CROSS_ORIGIN_CODE})
    else:
        response = Response(message, content_type="text/plain; charset=utf-8")
    response.status_code = 403
    response.headers["Cache-Control"] = "no-store"
    return response


def exempt_blueprint_from_csrf(app: Flask, blueprint_name: str) -> None:
    """For a blueprint that refuses browsers by other means (the internal P2P API's
    loopback and custom-header checks): its callers have no session to hold a token."""
    app.config.setdefault("CSRF_EXEMPT_BLUEPRINTS", set()).add(blueprint_name)


def init_request_security(app: Flask) -> None:
    app.config.setdefault("CSRF_ENABLED", True)
    app.config.setdefault("CSRF_EXEMPT_BLUEPRINTS", set())
    app.config.setdefault("WEB_ALLOWED_HOSTS", configured_allowed_hosts())
    allowed_hosts = frozenset(
        str(name).lower()
        for name in [*app.config["WEB_ALLOWED_HOSTS"], *(app.config.get("TRUSTED_HOSTS") or [])]
    )

    @app.before_request
    def refuse_foreign_request() -> Response | None:
        """Refuse DNS-rebinding requests (a `Host` that isn't this server) and, for
        methods that change something, requests sent by another site's page.

        Browsers send `Sec-Fetch-Site` and `Origin` on cross-site fetch, HTMX, and
        form posts alike. Requests with neither header come from non-browser
        clients (sync workers, curl, the test client) and pass."""
        if not _host_is_allowed(request_hostname(), allowed_hosts):
            abort(400)
        if request.method in _SAFE_METHODS:
            return None
        if _is_cross_origin():
            return _cross_origin_failure()
        return None

    @app.before_request
    def protect_unsafe_request() -> Response | None:
        if (
            request.method in _SAFE_METHODS
            or not app.config["CSRF_ENABLED"]
            or app.config.get("TESTING")
            or app.config.get("DEMO_MODE")
            or request.blueprint in app.config["CSRF_EXEMPT_BLUEPRINTS"]
        ):
            return None
        if csrf_is_valid():
            return None
        return _csrf_failure()

    @app.context_processor
    def inject_request_security() -> dict[str, str]:
        return {"csrf_token": csrf_token()}
