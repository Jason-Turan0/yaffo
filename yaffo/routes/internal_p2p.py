"""`/internal/p2p/v1`: the internal P2P API (docs/development/peer-sync.md).

Sync workers can't run the P2P engine (it owns the device's hub socket, UDP port,
and mDNS name), so they ask this web process to make peer calls through its one
engine. The API serves local Yaffo processes only — never a browser — and can
read the paired devices and pull from a granted scope, nothing more: pairing,
grants, revocation, and previews stay UI routes.

Three checks refuse everything else (`403 internal_only`):

- the caller is on loopback (keeps out other machines when the server is bound
  more widely);
- the `Host` names loopback (defeats DNS rebinding, even when web.allowed_hosts
  lists other names for the UI);
- `X-Yaffo-Internal: 1` is present (a cross-site page can't add a custom header
  without a CORS preflight, which these routes never answer).

Errors are `{"error": {"code": ..., "message": ...}}` with the status in
yaffo.p2p.peer_api.ERROR_STATUS.
"""
from __future__ import annotations

import json
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path

from flask import Blueprint, Flask, current_app, jsonify, request

from yaffo.db import db
from yaffo.db.models import TRUST_STATE_TRUSTED
from yaffo.db.repositories import media_dir_repository, p2p_repository
from yaffo.domain.media_filter_params import validate_wire_filters
from yaffo.p2p.errors import P2PServiceError
from yaffo.p2p.peer_api import (
    API_VERSION,
    BAD_DESTINATION,
    ENGINE_UNAVAILABLE,
    FORBIDDEN,
    INVALID_REQUEST,
    NOT_TRUSTED,
    PEER_OFFLINE,
    RELAY_BUDGET,
    RELAY_POLICIES,
    SCOPE_NOT_SHARED,
    PeerApiError,
    files_page,
    parse_scope_key,
    peer_denial_code,
    scope_key,
)
from yaffo.p2p.handlers.sharing import MAX_LIST_FILES_LIMIT
from yaffo.p2p.signaling import CallError
from yaffo.p2p.transfers import RELAY_BATCH_BUDGET_BYTES
from yaffo.process_status import register_web_extra
from yaffo.security import LOOPBACK_HOSTS, exempt_blueprint_from_csrf, request_hostname
from yaffo.sync.files import TEMP_PREFIX, TEMP_SUFFIX

INTERNAL_HEADER = "X-Yaffo-Internal"
LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1"})

internal_p2p = Blueprint("internal_p2p", __name__, url_prefix="/internal/p2p/v1")

# The port this server actually listens on, from the first request it serves
# (the web heartbeat starts on that request too). Published for workers.
_served_port: dict[str, int] = {}


def _error(code: str, message: str):
    error = PeerApiError(code, message)
    return jsonify({"error": {"code": code, "message": message}}), error.status


@internal_p2p.errorhandler(PeerApiError)
def _api_error(exc: PeerApiError):
    return _error(exc.code, exc.message)


def _caller_address() -> str:
    # ProxyFix rewrites remote_addr from X-Forwarded-For; the real peer of the
    # socket is what decides "this machine".
    original = request.environ.get("werkzeug.proxy_fix.orig", {})
    return original.get("REMOTE_ADDR") or request.remote_addr or ""


@internal_p2p.before_request
def refuse_everything_but_local_processes():
    if (
        _caller_address() not in LOOPBACK_ADDRESSES
        or request_hostname() not in LOOPBACK_HOSTS
        or request.headers.get(INTERNAL_HEADER) != "1"
    ):
        return _error(FORBIDDEN, "this API serves local Yaffo processes only")
    return None


def _service():
    service = current_app.extensions.get("p2p_service")
    if service is None or service.identity is None:
        raise PeerApiError(ENGINE_UNAVAILABLE, "device sharing is off")
    return service


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise PeerApiError(INVALID_REQUEST, "send a JSON object")
    return data


def _trusted_device(device_id: str):
    device = p2p_repository.get_known_device(db.session, device_id)
    if device is None or device.trust_state != TRUST_STATE_TRUSTED:
        raise PeerApiError(NOT_TRUSTED, "not a trusted paired device")
    return device


def _filters_arg(value: str | None) -> dict:
    """`filters`: a JSON object of sharing wire filters (a sync's subset of the
    scope); none means the whole scope. The serving device checks them again."""
    if not value:
        return {}
    try:
        filters = json.loads(value)
    except ValueError:
        raise PeerApiError(INVALID_REQUEST, "filters must be a JSON object") from None
    problem = validate_wire_filters(filters)
    if problem is not None:
        raise PeerApiError(INVALID_REQUEST, problem)
    return filters


def _int_arg(value, field: str, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    if isinstance(value, bool):
        raise PeerApiError(INVALID_REQUEST, f"{field} must be a whole number")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise PeerApiError(INVALID_REQUEST, f"{field} must be a whole number") from None
    if number < 0:
        raise PeerApiError(INVALID_REQUEST, f"{field} must not be negative")
    return number


def _peer_call_error(exc: Exception, default: str) -> PeerApiError:
    if isinstance(exc, (CallError, FutureTimeoutError)):
        return PeerApiError(PEER_OFFLINE, str(exc) or "the device did not answer")
    return PeerApiError(peer_denial_code(str(exc), default), str(exc))


# ---- status and devices --------------------------------------------------------


@internal_p2p.route("/status", methods=["GET"])
def status():
    service = current_app.extensions.get("p2p_service")
    available = service is not None and service.identity is not None
    return jsonify({
        "available": available,
        "device_id": service.identity.device_id if available else None,
        "hub_connected": service.hub_connected if available else False,
        "version": API_VERSION,
    })


@internal_p2p.route("/devices", methods=["GET"])
def devices():
    service = _service()
    try:
        online = service.connected_device_ids()
    except Exception:  # noqa: BLE001 — unknown presence is reported as null, not an error
        online = None
    local = service.local_device_ids()
    rows = []
    for device in p2p_repository.list_known_devices(db.session):
        rows.append({
            "device_id": device.device_id,
            "display_name": device.display_name,
            "trust_state": device.trust_state,
            "online": None if online is None else device.device_id in online,
            "local": device.device_id in local,
            "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        })
    return jsonify({"devices": rows})


@internal_p2p.route("/devices/<device_id>/scopes", methods=["GET"])
def scopes(device_id: str):
    service = _service()
    _trusted_device(device_id)
    try:
        response = service.list_shared.send(device_id)
    except (CallError, FutureTimeoutError, P2PServiceError) as exc:
        raise _peer_call_error(exc, SCOPE_NOT_SHARED) from exc
    return jsonify({
        "protocol": int(response.get("protocol") or 1),
        "scopes": [{**scope, "scope_key": scope_key(scope)} for scope in response.get("scopes") or []],
    })


@internal_p2p.route("/devices/<device_id>/files", methods=["GET"])
def files(device_id: str):
    service = _service()
    _trusted_device(device_id)
    scope = parse_scope_key(request.args.get("scope", ""))
    cursor = _int_arg(request.args.get("cursor"), "cursor", default=0)
    limit = min(_int_arg(request.args.get("limit"), "limit", default=MAX_LIST_FILES_LIMIT), MAX_LIST_FILES_LIMIT)
    filters = _filters_arg(request.args.get("filters"))
    session_id = request.args.get("session")
    if session_id:
        return jsonify(service.peer_api.list_files(session_id, scope, cursor, limit, filters))
    try:
        response = service.list_files.send(
            device_id, scope.media_dir_id, scope.relative_path, filters, cursor, limit, album_id=scope.album_id
        )
    except (CallError, FutureTimeoutError, P2PServiceError) as exc:
        raise _peer_call_error(exc, SCOPE_NOT_SHARED) from exc
    return jsonify(files_page(response, cursor))


# ---- sessions ------------------------------------------------------------------


@internal_p2p.route("/devices/<device_id>/sessions", methods=["POST"])
def session_open(device_id: str):
    service = _service()
    _trusted_device(device_id)
    body = _body()
    relay = body.get("relay", "never")
    if relay not in RELAY_POLICIES:
        raise PeerApiError(INVALID_REQUEST, f"relay must be one of {', '.join(RELAY_POLICIES)}")
    budget = _int_arg(body.get("relay_budget_bytes"), "relay_budget_bytes", default=RELAY_BATCH_BUDGET_BYTES)
    if relay != RELAY_BUDGET:
        budget = 0
    owner = str(body.get("owner") or "").strip()
    if not owner:
        raise PeerApiError(INVALID_REQUEST, "owner is required")
    return jsonify(service.peer_api.open_session(device_id, relay, budget, owner)), 201


@internal_p2p.route("/sessions/<session_id>", methods=["GET"])
def session_info(session_id: str):
    return jsonify(_service().peer_api.session_info(session_id))


@internal_p2p.route("/sessions/<session_id>", methods=["DELETE"])
def session_close(session_id: str):
    _service().peer_api.close_session(session_id, request.args.get("owner"))
    return "", 204


# ---- pulls ---------------------------------------------------------------------


def validate_destination(raw) -> Path:
    """The security boundary of the write: whatever gets past the request checks
    still can only create a sync temp file in an existing folder inside a
    configured media dir."""
    if not isinstance(raw, str) or not raw:
        raise PeerApiError(BAD_DESTINATION, "destination is required")
    destination = Path(raw)
    if not destination.is_absolute():
        raise PeerApiError(BAD_DESTINATION, "destination must be an absolute path")
    name = destination.name
    if not (name.startswith(TEMP_PREFIX) and name.endswith(TEMP_SUFFIX)) or len(name) <= len(TEMP_PREFIX + TEMP_SUFFIX):
        raise PeerApiError(BAD_DESTINATION, "destination must be a sync temporary file name")
    for path in (destination, destination.with_name(f"{name}.partial"), destination.with_name(f"{name}.partial.json")):
        if path.is_symlink():
            raise PeerApiError(BAD_DESTINATION, "destination must not be a symbolic link")
    parent = destination.parent
    if not parent.is_dir():
        raise PeerApiError(BAD_DESTINATION, "destination folder must exist")
    resolved = parent.resolve() / name
    for root in media_dir_repository.get_media_dirs(db.session):
        try:
            resolved.relative_to(Path(root).expanduser().resolve())
            return resolved
        except ValueError:
            continue
    raise PeerApiError(BAD_DESTINATION, "destination must be inside a configured media folder")


@internal_p2p.route("/sessions/<session_id>/pulls", methods=["POST"])
def pull_start(session_id: str):
    service = _service()
    body = _body()
    media_item_id = _int_arg(body.get("media_item_id"), "media_item_id")
    source = body.get("source_id")
    if not isinstance(source, str) or not source:
        raise PeerApiError(INVALID_REQUEST, "source_id is required")
    expected = body.get("expected") or {}
    if not isinstance(expected, dict):
        raise PeerApiError(INVALID_REQUEST, "expected must be an object")
    size = _int_arg(expected.get("size"), "expected.size", default=0)
    try:
        mtime = float(expected.get("mtime") or 0)
    except (TypeError, ValueError):
        raise PeerApiError(INVALID_REQUEST, "expected.mtime must be a number") from None
    destination = validate_destination(body.get("destination"))
    return jsonify(service.peer_api.start_pull(session_id, media_item_id, source, size, mtime, destination)), 202


@internal_p2p.route("/pulls/<pull_id>", methods=["GET"])
def pull_status(pull_id: str):
    try:
        wait = float(request.args.get("wait") or 0)
    except ValueError:
        raise PeerApiError(INVALID_REQUEST, "wait must be a number of seconds") from None
    return jsonify(_service().peer_api.pull_status(pull_id, wait))


@internal_p2p.route("/pulls/<pull_id>", methods=["DELETE"])
def pull_cancel(pull_id: str):
    _service().peer_api.cancel_pull(pull_id)
    return "", 204


# ---- registration ----------------------------------------------------------------


def _remember_served_port() -> None:
    if "port" in _served_port:
        return
    original = request.environ.get("werkzeug.proxy_fix.orig", {})
    raw = original.get("SERVER_PORT") or request.environ.get("SERVER_PORT")
    try:
        _served_port["port"] = int(raw)
    except (TypeError, ValueError):
        pass


def internal_api_status() -> dict:
    """What the web heartbeat publishes so a worker can find this API."""
    if "port" not in _served_port:
        raise LookupError("no request served yet")
    return {"port": _served_port["port"], "version": API_VERSION}


def init_internal_p2p_routes(app: Flask) -> None:
    app.register_blueprint(internal_p2p)
    exempt_blueprint_from_csrf(app, internal_p2p.name)
    app.before_request(_remember_served_port)
    register_web_extra("internal_api", internal_api_status)
