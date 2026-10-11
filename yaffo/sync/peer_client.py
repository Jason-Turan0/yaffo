"""A client for the internal P2P API (`/internal/p2p/v1`, docs/development/peer-sync.md).

Sync workers can't run the P2P engine, so the peer source asks the web process,
which owns it. Two transports, one interface:

- **HTTP** (sync workers): the web process's port comes from its heartbeat
  (`web_status.json` → `extra.internal_api`), and every call carries
  `X-Yaffo-Internal: 1` to `127.0.0.1`.
- **In-process** (the web process itself: the option pickers and Test): the same
  routes through Flask's test client, so the request checks and error mapping are
  exactly the ones a worker gets, without a socket.

Errors raise PeerClientError with the API's stable code. A web process that isn't
running (no heartbeat, refused connection) counts as `peer_offline`: the run ends
quietly and the next one tries again.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional
from urllib.parse import quote

import requests

from yaffo.process_status import read_status

INTERNAL_HEADER = {"X-Yaffo-Internal": "1"}
BASE_PATH = "/internal/p2p/v1"
# A heartbeat older than this means the web process is gone.
HEARTBEAT_STALE_SECONDS = 30.0
DEFAULT_TIMEOUT_SECONDS = 20.0
OPEN_SESSION_TIMEOUT_SECONDS = 75.0
MAX_WAIT_SECONDS = 10

PEER_OFFLINE = "peer_offline"
ENGINE_UNAVAILABLE = "engine_unavailable"
SESSION_EXPIRED = "session_expired"


class PeerClientError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


# A transport sends (method, path, params, json body, timeout) and returns
# (status, decoded JSON body or None).
Transport = Callable[[str, str, Optional[dict], Optional[dict], float], tuple[int, Any]]


def http_transport(data_dir=None) -> Transport:
    def send(method: str, path: str, params: Optional[dict], body: Optional[dict], timeout: float):
        port = _web_port(data_dir)
        try:
            response = requests.request(method, f"http://127.0.0.1:{port}{path}", params=params, json=body,
                                        headers=INTERNAL_HEADER, timeout=timeout)
        except requests.RequestException as exc:
            raise PeerClientError(PEER_OFFLINE, f"Yaffo isn't answering: {exc}") from exc
        try:
            payload = response.json() if response.content else None
        except ValueError:
            payload = None
        return response.status_code, payload

    return send


def _web_port(data_dir=None) -> int:
    status = read_status("web", **({"data_dir": data_dir} if data_dir is not None else {}))
    api = ((status or {}).get("extra") or {}).get("internal_api") or {}
    if not status or time.time() - status["beat_at"] > HEARTBEAT_STALE_SECONDS or not api.get("port"):
        raise PeerClientError(PEER_OFFLINE, "Yaffo isn't running")
    return int(api["port"])


def in_process_transport(app) -> Transport:
    def send(method: str, path: str, params: Optional[dict], body: Optional[dict], timeout: float):
        client = app.test_client()
        response = client.open(path, method=method, query_string=params, json=body, headers=INTERNAL_HEADER,
                               environ_base={"REMOTE_ADDR": "127.0.0.1"})
        return response.status_code, response.get_json(silent=True)

    return send


def default_transport() -> Transport:
    """In-process when this process owns the P2P engine, otherwise HTTP."""
    try:
        from flask import current_app, has_app_context
        if has_app_context() and current_app.extensions.get("p2p_service") is not None:
            return in_process_transport(current_app._get_current_object())
    except RuntimeError:
        pass
    return http_transport()


class PeerClient:
    def __init__(self, transport: Optional[Transport] = None) -> None:
        self._transport = transport or default_transport()

    def _call(self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None,
              timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
        status, payload = self._transport(method, BASE_PATH + path, params, body, timeout)
        if status >= 400:
            error = (payload or {}).get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                raise PeerClientError(str(error.get("code") or "internal_error"), str(error.get("message") or ""))
            raise PeerClientError("internal_error", f"HTTP {status}")
        return payload

    def status(self) -> dict:
        return self._call("GET", "/status")

    def devices(self) -> list[dict]:
        return self._call("GET", "/devices")["devices"]

    def scopes(self, device_id: str) -> dict:
        return self._call("GET", f"/devices/{quote(device_id, safe='')}/scopes")

    def files(self, device_id: str, scope_key: str, cursor: Optional[str] = None, limit: int = 200,
              session_id: Optional[str] = None, filters: Optional[dict] = None) -> dict:
        params = {"scope": scope_key, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if session_id:
            params["session"] = session_id
        if filters:
            params["filters"] = json.dumps(filters, sort_keys=True)
        return self._call("GET", f"/devices/{quote(device_id, safe='')}/files", params=params)

    def open_session(self, device_id: str, owner: str, relay: str = "never",
                     relay_budget_bytes: Optional[int] = None) -> dict:
        body: dict[str, Any] = {"owner": owner, "relay": relay}
        if relay_budget_bytes is not None:
            body["relay_budget_bytes"] = relay_budget_bytes
        return self._call("POST", f"/devices/{quote(device_id, safe='')}/sessions", body=body,
                          timeout=OPEN_SESSION_TIMEOUT_SECONDS)

    def session(self, session_id: str) -> dict:
        return self._call("GET", f"/sessions/{quote(session_id, safe='')}")

    def close_session(self, session_id: str, owner: Optional[str] = None) -> None:
        self._call("DELETE", f"/sessions/{quote(session_id, safe='')}", params={"owner": owner} if owner else None)

    def start_pull(self, session_id: str, media_item_id: int, source_id: str, size: int, mtime: float,
                   destination: str) -> str:
        body = {"media_item_id": media_item_id, "source_id": source_id,
                "expected": {"size": size, "mtime": mtime}, "destination": destination}
        return self._call("POST", f"/sessions/{quote(session_id, safe='')}/pulls", body=body)["pull_id"]

    def pull(self, pull_id: str, wait: float = 0) -> dict:
        wait = max(0, min(wait, MAX_WAIT_SECONDS))
        return self._call("GET", f"/pulls/{quote(pull_id, safe='')}", params={"wait": wait},
                          timeout=wait + DEFAULT_TIMEOUT_SECONDS)

    def cancel_pull(self, pull_id: str) -> None:
        self._call("DELETE", f"/pulls/{quote(pull_id, safe='')}")
