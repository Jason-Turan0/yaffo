from __future__ import annotations

import os
import secrets
import threading
import time as monotonic_time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from flask import Flask, Response, current_app, jsonify, render_template, request, session
from flask_babel import gettext
from yaffo.security import csrf_is_valid, request_expects_json

DEMO_ROLE_SOURCE = "source"
DEMO_ROLE_RECEIVER = "receiver"
DEMO_ROLES = frozenset({DEMO_ROLE_SOURCE, DEMO_ROLE_RECEIVER})

DEMO_DISABLED_CODE = "demo_feature_disabled"
DEMO_CSRF_CODE = "csrf_failed"
DEMO_RATE_LIMIT_CODE = "demo_rate_limit_exceeded"

DEMO_PUBLIC_READ_ENDPOINTS = frozenset(
    {
        "albums_index",
        "albums_show",
        "automations_edit",
        "automations_edit_triggers",
        "automations_index",
        "automations_runs",
        "automations_show",
        "automations_status",
        "automations_validate_cron",
        "face_thumbnail",
        "faces_index",
        "favicon",
        "get_tag_values",
        "index",
        "location_autocomplete",
        "locations_list",
        "media",
        "media_poster",
        "media_view",
        "pages_detail",
        "pages_design",
        "pages_widget_frame",
        "people_list",
        "person_faces",
        "placeholder",
        "sharing_device",
        "sharing_device_files",
        "sharing_device_preview",
        "sharing_device_tag_values",
        "sharing_device_transfers",
        "sharing_index",
        "sharing_settings",
        "sharing_settings_section",
        "sharing_sidebar",
        "sharing_sidebar_shared_with_me",
        "static",
        "theme_css",
        "theme_preview_css",
        "theme_tokens_css",
        "themes_index",
        "themes_show",
        "themes_status",
        "utilities_index",
        "utilities_index_photos",
        "utilities_index_photos_scan",
        "utilities_remove_duplicates",
        "utilities_remove_duplicates_results",
    }
)

DEMO_UNSAFE_ENDPOINT_ROLES = {
    "faces_assign": DEMO_ROLES,
    "pages_version_widget_query": DEMO_ROLES,
    "sharing_device_pull_selected": frozenset({DEMO_ROLE_RECEIVER}),
}

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_DEMO_ROLE_ATTRIBUTE = "_yaffo_demo_roles"
_DEMO_SESSION_KEY = "_yaffo_demo_session"
_STATIC_ENDPOINTS = frozenset(
    {"static", "favicon", "theme_css", "theme_preview_css", "theme_tokens_css"}
)
_OSM_TILE_ORIGIN = "https://tile.openstreetmap.org"
_BYTE_LIMITED_ENDPOINTS = frozenset(
    {"face_thumbnail", "media", "media_poster", "sharing_device_preview"}
)


class _WindowLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[tuple[float, int]]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int, cost: int = 1) -> bool:
        now = monotonic_time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0][0] <= cutoff:
                events.popleft()
            if sum(event_cost for _timestamp, event_cost in events) + cost > limit:
                return False
            events.append((now, cost))
            return True


def demo_unsafe_allowed(*roles: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    allowed_roles = frozenset(roles)
    if not allowed_roles or not allowed_roles.issubset(DEMO_ROLES):
        raise ValueError("demo unsafe-method exceptions require a valid demo role")

    def decorate(view: Callable[..., Any]) -> Callable[..., Any]:
        setattr(view, _DEMO_ROLE_ATTRIBUTE, allowed_roles)
        return view

    return decorate


def is_demo_mode() -> bool:
    return bool(current_app.config.get("DEMO_MODE"))


def _parse_bool(value: str | None) -> bool:
    return value == "1"


def _env_positive_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


def configure_demo(app: Flask) -> None:
    app.config.setdefault("DEMO_MODE", _parse_bool(os.environ.get("YAFFO_DEMO_MODE")))
    app.config.setdefault("DEMO_ROLE", os.environ.get("YAFFO_DEMO_ROLE"))
    app.config.setdefault("DEMO_TIMEZONE", "America/Chicago")
    app.config.setdefault("DEMO_RESET_TIME", "07:45")
    app.config.setdefault(
        "DEMO_REQUESTS_PER_SESSION_MINUTE",
        _env_positive_int("YAFFO_DEMO_REQUESTS_PER_SESSION_MINUTE", 180),
    )
    app.config.setdefault(
        "DEMO_REQUESTS_PER_IP_MINUTE",
        _env_positive_int("YAFFO_DEMO_REQUESTS_PER_IP_MINUTE", 360),
    )
    app.config.setdefault(
        "DEMO_REQUESTS_GLOBAL_MINUTE",
        _env_positive_int("YAFFO_DEMO_REQUESTS_GLOBAL_MINUTE", 900),
    )
    app.config.setdefault(
        "DEMO_WIDGET_QUERIES_PER_SESSION_MINUTE",
        _env_positive_int("YAFFO_DEMO_WIDGET_QUERIES_PER_SESSION_MINUTE", 30),
    )
    app.config.setdefault(
        "DEMO_WIDGET_QUERIES_PER_IP_MINUTE",
        _env_positive_int("YAFFO_DEMO_WIDGET_QUERIES_PER_IP_MINUTE", 60),
    )
    app.config.setdefault(
        "DEMO_WIDGET_QUERIES_GLOBAL_MINUTE",
        _env_positive_int("YAFFO_DEMO_WIDGET_QUERIES_GLOBAL_MINUTE", 120),
    )
    app.config.setdefault(
        "DEMO_TRANSFER_COOLDOWN_SECONDS",
        _env_positive_int("YAFFO_DEMO_TRANSFER_COOLDOWN_SECONDS", 300),
    )
    app.config.setdefault(
        "DEMO_SCAN_COOLDOWN_SECONDS",
        _env_positive_int("YAFFO_DEMO_SCAN_COOLDOWN_SECONDS", 60),
    )
    app.config.setdefault(
        "DEMO_SCANS_GLOBAL_PER_COOLDOWN",
        _env_positive_int("YAFFO_DEMO_SCANS_GLOBAL_PER_COOLDOWN", 4),
    )
    app.config.setdefault(
        "DEMO_SCAN_MAX_FILES",
        _env_positive_int("YAFFO_DEMO_SCAN_MAX_FILES", 10_000),
    )
    app.config.setdefault(
        "DEMO_SCAN_MAX_SECONDS",
        _env_positive_int("YAFFO_DEMO_SCAN_MAX_SECONDS", 15),
    )
    app.config.setdefault(
        "DEMO_MEDIA_BYTES_PER_SESSION_MINUTE",
        _env_positive_int("YAFFO_DEMO_MEDIA_BYTES_PER_SESSION_MINUTE", 64 * 1024 * 1024),
    )
    app.config.setdefault(
        "DEMO_MEDIA_BYTES_PER_IP_MINUTE",
        _env_positive_int("YAFFO_DEMO_MEDIA_BYTES_PER_IP_MINUTE", 128 * 1024 * 1024),
    )
    app.config.setdefault(
        "DEMO_MEDIA_BYTES_GLOBAL_MINUTE",
        _env_positive_int("YAFFO_DEMO_MEDIA_BYTES_GLOBAL_MINUTE", 512 * 1024 * 1024),
    )
    app.config.setdefault(
        "DEMO_MEDIA_BYTES_GLOBAL_DAY",
        _env_positive_int("YAFFO_DEMO_MEDIA_BYTES_GLOBAL_DAY", 5 * 1024 * 1024 * 1024),
    )

    if not app.config["DEMO_MODE"]:
        app.config["DEMO_ROLE"] = None
        return

    if app.config["DEMO_ROLE"] not in DEMO_ROLES:
        raise RuntimeError("YAFFO_DEMO_ROLE must be 'source' or 'receiver' in demo mode")
    if not app.config.get("TESTING") and app.secret_key == "dev-secret-key-change-in-production":
        raise RuntimeError("SECRET_KEY must be set to a unique non-development value in demo mode")

    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=1_048_576,
    )


def validate_demo_route_map(app: Flask) -> None:
    rules_by_endpoint: dict[str, list[Any]] = {}
    for rule in app.url_map.iter_rules():
        rules_by_endpoint.setdefault(rule.endpoint, []).append(rule)

    missing_reads = DEMO_PUBLIC_READ_ENDPOINTS - rules_by_endpoint.keys()
    if missing_reads:
        raise RuntimeError(f"demo read policy names missing endpoints: {sorted(missing_reads)}")

    for endpoint, expected_roles in DEMO_UNSAFE_ENDPOINT_ROLES.items():
        rules = rules_by_endpoint.get(endpoint)
        if not rules:
            raise RuntimeError(f"demo unsafe-method policy names missing endpoint: {endpoint}")
        if not any((rule.methods or set()) - _SAFE_METHODS for rule in rules):
            raise RuntimeError(f"demo exception endpoint has no unsafe method: {endpoint}")
        view = app.view_functions[endpoint]
        actual_roles = getattr(view, _DEMO_ROLE_ATTRIBUTE, frozenset())
        if actual_roles != expected_roles:
            raise RuntimeError(
                f"demo exception roles for {endpoint} are {sorted(actual_roles)}, "
                f"expected {sorted(expected_roles)}"
            )


def _blocked_response(message: str, code: str, status: int = 403) -> Response:
    if request_expects_json():
        response = jsonify({"error": message, "code": code})
        response.status_code = status
    else:
        response = Response(
            render_template("demo/feature_disabled.html", message=message),
            status=status,
            content_type="text/html; charset=utf-8",
        )
    response.headers["Cache-Control"] = "no-store"
    return response


def _rate_limit_gate() -> Response | None:
    if request.endpoint in _STATIC_ENDPOINTS:
        return None
    limiter: _WindowLimiter = current_app.extensions["demo_rate_limiter"]
    visitor_id = session.get(_DEMO_SESSION_KEY)
    if not visitor_id:
        visitor_id = secrets.token_urlsafe(18)
        session[_DEMO_SESSION_KEY] = visitor_id
    ip_address = request.remote_addr or "unknown"

    checks = [
        (
            f"request:session:{visitor_id}",
            current_app.config["DEMO_REQUESTS_PER_SESSION_MINUTE"],
            60,
        ),
        (f"request:ip:{ip_address}", current_app.config["DEMO_REQUESTS_PER_IP_MINUTE"], 60),
        ("request:global", current_app.config["DEMO_REQUESTS_GLOBAL_MINUTE"], 60),
    ]
    if request.endpoint == "pages_version_widget_query":
        checks.extend(
            [
                (
                    f"widget:session:{visitor_id}",
                    current_app.config["DEMO_WIDGET_QUERIES_PER_SESSION_MINUTE"],
                    60,
                ),
                (
                    f"widget:ip:{ip_address}",
                    current_app.config["DEMO_WIDGET_QUERIES_PER_IP_MINUTE"],
                    60,
                ),
                ("widget:global", current_app.config["DEMO_WIDGET_QUERIES_GLOBAL_MINUTE"], 60),
            ]
        )
    if request.endpoint == "sharing_device_pull_selected":
        cooldown = current_app.config["DEMO_TRANSFER_COOLDOWN_SECONDS"]
        checks.extend(
            [
                (f"transfer:session:{visitor_id}", 1, cooldown),
                (f"transfer:ip:{ip_address}", 1, cooldown),
            ]
        )
    if request.endpoint == "utilities_index_photos_scan":
        cooldown = current_app.config["DEMO_SCAN_COOLDOWN_SECONDS"]
        checks.extend(
            [
                (f"scan:session:{visitor_id}", 1, cooldown),
                (f"scan:ip:{ip_address}", 1, cooldown),
                (
                    "scan:global",
                    current_app.config["DEMO_SCANS_GLOBAL_PER_COOLDOWN"],
                    cooldown,
                ),
            ]
        )

    if all(limiter.allow(key, int(limit), window) for key, limit, window in checks):
        return None
    return _blocked_response(
        gettext("The public demo is busy. Please wait a moment and try again."),
        DEMO_RATE_LIMIT_CODE,
        status=429,
    )


def _response_content_length(response: Response) -> int:
    raw_length = response.headers.get("Content-Length")
    if raw_length is None:
        return 0
    try:
        return max(int(raw_length), 0)
    except ValueError:
        return 0


def _apply_media_byte_limits(response: Response) -> Response:
    if (
        request.method == "HEAD"
        or request.endpoint not in _BYTE_LIMITED_ENDPOINTS
        or not 200 <= response.status_code < 300
    ):
        return response
    content_length = _response_content_length(response)
    if content_length == 0:
        return response

    visitor_id = session.get(_DEMO_SESSION_KEY)
    if not visitor_id:
        return response
    ip_address = request.remote_addr or "unknown"
    limiter: _WindowLimiter = current_app.extensions["demo_rate_limiter"]
    checks = [
        (
            f"media:session:{visitor_id}",
            current_app.config["DEMO_MEDIA_BYTES_PER_SESSION_MINUTE"],
            60,
        ),
        (
            f"media:ip:{ip_address}",
            current_app.config["DEMO_MEDIA_BYTES_PER_IP_MINUTE"],
            60,
        ),
        (
            "media:global:minute",
            current_app.config["DEMO_MEDIA_BYTES_GLOBAL_MINUTE"],
            60,
        ),
        (
            "media:global:day",
            current_app.config["DEMO_MEDIA_BYTES_GLOBAL_DAY"],
            86_400,
        ),
    ]
    if all(
        limiter.allow(key, int(limit), window, cost=content_length)
        for key, limit, window in checks
    ):
        return response
    response.close()
    return _blocked_response(
        gettext("The public demo is busy. Please wait a moment and try again."),
        DEMO_RATE_LIMIT_CODE,
        status=429,
    )


def _demo_gate() -> Response | None:
    endpoint = request.endpoint
    if endpoint is None:
        return _blocked_response(
            gettext("This action is disabled in the public demo."),
            DEMO_DISABLED_CODE,
        )

    if request.method in _SAFE_METHODS:
        if endpoint in DEMO_PUBLIC_READ_ENDPOINTS:
            return None
        return _blocked_response(
            gettext("This action is disabled in the public demo."),
            DEMO_DISABLED_CODE,
        )

    view = current_app.view_functions.get(endpoint)
    roles = getattr(view, _DEMO_ROLE_ATTRIBUTE, frozenset()) if view is not None else frozenset()
    if current_app.config["DEMO_ROLE"] not in roles:
        return _blocked_response(
            gettext("This action is disabled in the public demo."),
            DEMO_DISABLED_CODE,
        )
    if not csrf_is_valid():
        return _blocked_response(
            gettext("This request could not be verified. Refresh the page and try again."),
            DEMO_CSRF_CODE,
        )
    return None


def _next_reset_at() -> str:
    timezone = ZoneInfo(current_app.config["DEMO_TIMEZONE"])
    hour_text, minute_text = current_app.config["DEMO_RESET_TIME"].split(":", maxsplit=1)
    reset_time = time(hour=int(hour_text), minute=int(minute_text))
    now = datetime.now(timezone)
    next_reset = datetime.combine(now.date(), reset_time, timezone)
    if next_reset <= now:
        next_reset += timedelta(days=1)
    return next_reset.isoformat()


def init_demo_boundary(app: Flask) -> None:
    if not app.config["DEMO_MODE"]:
        return

    validate_demo_route_map(app)
    app.extensions["demo_rate_limiter"] = _WindowLimiter()
    app.before_request(_demo_gate)
    app.before_request(_rate_limit_gate)

    @app.context_processor
    def inject_demo_context() -> dict[str, Any]:
        return {
            "demo_mode": True,
            "demo_role": app.config["DEMO_ROLE"],
            "demo_next_reset_at": _next_reset_at(),
            "demo_timezone": app.config["DEMO_TIMEZONE"],
        }

    @app.after_request
    def add_demo_security_headers(response: Response) -> Response:
        response = _apply_media_byte_limits(response)
        external_images = f" {_OSM_TILE_ORIGIN}" if request.endpoint == "locations_list" else ""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
            f"img-src 'self' data: blob:{external_images}; "
            "media-src 'self' blob:; object-src 'none'; "
            "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self' wss://hub.yaffo.app",
        )
        return response
