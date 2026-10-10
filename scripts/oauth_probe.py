#!/usr/bin/env python3
"""Check an OAuth app registration for the sync presets, end to end.

Phase 0 of docs/development/automation-sync-adapters.md registers a Microsoft
Entra app (OneDrive) and a Google Cloud OAuth client (Google Photos). This
script proves a registration works the way the presets will use it: a desktop
(public) client signing in through the browser with PKCE and a loopback
return address, then:

1. one read call to the provider's API with the access token,
2. one refresh-token exchange (offline access works).

Tokens are never printed or stored. Run (one line):
    python -m scripts.oauth_probe microsoft --client-id <id>
    python -m scripts.oauth_probe google --client-id <id> --client-secret <secret>

The client id and secret can also come from YAFFO_OAUTH_<PROVIDER>_CLIENT_ID and
YAFFO_OAUTH_<PROVIDER>_CLIENT_SECRET. Google issues a secret even for desktop
clients; it ships inside the app and isn't confidential, but its token
endpoint expects it.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import secrets
import sys
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

import requests

# The return address is the bare loopback root, http://<host>:<port>. Both
# providers ignore the port on a loopback address, but Microsoft still matches
# the path against the registered URI (http://localhost, no path).
CALLBACK_PATH = "/"
SIGN_IN_TIMEOUT_SECONDS = 300
HTTP_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class ProviderRegistration:
    name: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    # Microsoft public clients accept http://localhost on any port; Google
    # desktop clients accept the 127.0.0.1 literal on any port.
    loopback_host: str
    check_url: str
    extra_authorize_params: tuple[tuple[str, str], ...] = ()


PROVIDERS: dict[str, ProviderRegistration] = {
    "microsoft": ProviderRegistration(
        name="microsoft",
        authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        # Union of the OneDrive source (phase 2) and target (phase 3) needs.
        scopes=("offline_access", "Files.ReadWrite"),
        loopback_host="localhost",
        check_url="https://graph.microsoft.com/v1.0/me/drive/root/children?$top=5&$select=name",
    ),
    "google": ProviderRegistration(
        name="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        # The Library API scopes left after March 2025: upload, plus read and
        # edit of what the app itself created.
        scopes=(
            "https://www.googleapis.com/auth/photoslibrary.appendonly",
            "https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata",
            "https://www.googleapis.com/auth/photoslibrary.edit.appcreateddata",
        ),
        loopback_host="127.0.0.1",
        check_url="https://photoslibrary.googleapis.com/v1/albums?pageSize=5",
        # Google returns a refresh token only with access_type=offline, and
        # only on first consent unless prompt=consent.
        extra_authorize_params=(("access_type", "offline"), ("prompt", "consent")),
    ),
}


class ProbeError(Exception):
    """A step failed; the message says which and why, without secrets."""


@dataclass(frozen=True)
class Pkce:
    verifier: str
    challenge: str


def make_pkce() -> Pkce:
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return Pkce(verifier=verifier, challenge=challenge)


def build_authorize_url(
    provider: ProviderRegistration, client_id: str, redirect_uri: str, state: str, pkce: Pkce
) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(provider.scopes),
        "state": state,
        "code_challenge": pkce.challenge,
        "code_challenge_method": "S256",
        **dict(provider.extra_authorize_params),
    }
    return f"{provider.authorize_url}?{urlencode(params)}"


def parse_callback(query: str, expected_state: str) -> str:
    """Return the authorization code from the return-address query string."""
    params = {key: values[0] for key, values in parse_qs(query).items()}
    if params.get("state") != expected_state:
        raise ProbeError("sign-in returned a mismatched state; ignoring the response")
    if "error" in params:
        description = params.get("error_description", "")
        raise ProbeError(f"provider refused sign-in: {params['error']} {description}".strip())
    code = params.get("code")
    if not code:
        raise ProbeError("sign-in returned neither a code nor an error")
    return code


class _LoopbackServer(HTTPServer):
    callback_query: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _LoopbackServer

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        self.server.callback_query = parsed.query
        body = b"<p>Sign-in received. You can close this tab and return to the terminal.</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - base signature
        return


def wait_for_callback(server: _LoopbackServer, timeout_seconds: float) -> str:
    server.timeout = 1
    stop_at = threading.Event()
    timer = threading.Timer(timeout_seconds, stop_at.set)
    timer.start()
    try:
        while server.callback_query is None and not stop_at.is_set():
            server.handle_request()
    finally:
        timer.cancel()
    if server.callback_query is None:
        raise ProbeError(f"no sign-in response within {int(timeout_seconds)} seconds")
    return server.callback_query


def _token_request(
    http: requests.Session, provider: ProviderRegistration, data: dict[str, str]
) -> dict:
    response = http.post(provider.token_url, data=data, timeout=HTTP_TIMEOUT_SECONDS)
    payload = response.json() if response.content else {}
    if response.status_code != 200:
        error = payload.get("error", response.status_code)
        description = payload.get("error_description", "")
        raise ProbeError(f"token endpoint refused {data['grant_type']}: {error} {description}".strip())
    if "access_token" not in payload:
        raise ProbeError(f"token endpoint answered {data['grant_type']} without an access token")
    return payload


def run_probe(
    provider: ProviderRegistration,
    client_id: str,
    client_secret: str | None,
    *,
    open_browser: Callable[[str], object] = webbrowser.open,
    http: requests.Session | None = None,
    report: Callable[[str], None] = print,
    timeout_seconds: float = SIGN_IN_TIMEOUT_SECONDS,
) -> None:
    http = http or requests.Session()
    pkce = make_pkce()
    state = secrets.token_urlsafe(24)

    server = _LoopbackServer((provider.loopback_host, 0), _CallbackHandler)
    try:
        port = server.server_address[1]
        redirect_uri = f"http://{provider.loopback_host}:{port}"
        url = build_authorize_url(provider, client_id, redirect_uri, state, pkce)
        report(f"Opening the {provider.name} sign-in page (return address {redirect_uri}).")
        report(f"If no browser opens, visit:\n{url}")
        open_browser(url)
        query = wait_for_callback(server, timeout_seconds)
    finally:
        server.server_close()
    code = parse_callback(query, state)
    report("ok  sign-in returned an authorization code")

    secret_field = {"client_secret": client_secret} if client_secret else {}
    tokens = _token_request(http, provider, {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": pkce.verifier,
        **secret_field,
    })
    granted = tokens.get("scope", "")
    report(f"ok  code exchanged for an access token (granted: {granted or 'not reported'})")

    check = http.get(
        provider.check_url,
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    if check.status_code != 200:
        raise ProbeError(f"API check {provider.check_url} answered {check.status_code}: {check.text[:300]}")
    report(f"ok  API check answered 200 ({provider.check_url})")

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise ProbeError("no refresh token returned; offline access isn't working")
    _token_request(http, provider, {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
        **secret_field,
    })
    report("ok  refresh token exchanged for a new access token")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("provider", choices=sorted(PROVIDERS))
    parser.add_argument("--client-id")
    parser.add_argument("--client-secret")
    args = parser.parse_args(argv)

    env_prefix = f"YAFFO_OAUTH_{args.provider.upper()}"
    client_id = args.client_id or os.environ.get(f"{env_prefix}_CLIENT_ID")
    client_secret = args.client_secret or os.environ.get(f"{env_prefix}_CLIENT_SECRET")
    if not client_id:
        parser.error(f"pass --client-id or set {env_prefix}_CLIENT_ID")

    try:
        run_probe(PROVIDERS[args.provider], client_id, client_secret)
    except ProbeError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print(f"PASS {args.provider} registration works for desktop sign-in")
    return 0


if __name__ == "__main__":
    sys.exit(main())
