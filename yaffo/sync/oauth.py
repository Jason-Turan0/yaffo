"""OAuth 2 sign-in for connections, and the access tokens runs use.

Sign-in is the desktop-app flow (RFC 8252): the browser opens the provider's page
with PKCE (S256), and the provider sends it back to Yaffo's loopback return address,
`http://<loopback host>:<Yaffo's port>` with no path (Microsoft matches the path
of the registered `http://localhost`, so the return address is the bare root). The
web app's `/` answers it when the `state` names a sign-in it started. Then Yaffo
exchanges the code, asks the provider who signed in (`whoami`), and stores the
connection. When the browser can't reach Yaffo (Yaffo on a home server, opened from
a laptop), the device-code flow (RFC 8628) is the fallback: the user types a short
code on the provider's site while Yaffo polls.

Pending sign-ins are stored (pending_sign_ins.py), keyed by `state` or the polling
handle, so a restart of the web app between "Sign in" and the return doesn't lose
them; their secret half (the PKCE verifier or the device code) is in the keychain.
Tokens are in the keychain too, and refreshing one takes the connection's refresh
lease first, because providers that rotate refresh tokens would otherwise sign
concurrent refreshers out (see connections.py).
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from yaffo.logging_config import get_logger
from yaffo.sync import connections, pending_sign_ins, secrets_store
from yaffo.sync.pending_sign_ins import KIND_BROWSER, KIND_DEVICE, PendingSignIn
from yaffo.sync.providers import ProviderPreset, field_at, outgoing_url

logger = get_logger(__name__)

HTTP_TIMEOUT_SECONDS = 30
PENDING_TTL_SECONDS = 15 * 60
# Refresh this long before the token expires, so a page's downloads don't run out.
REFRESH_MARGIN_SECONDS = 10 * 60
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
WHOAMI_ATTEMPTS = 3
WHOAMI_RETRY_SECONDS = 1.0


class SignInError(Exception):
    """Sign-in failed. `code` is stable (shown translated); the message is for logs."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


class TokenUnavailable(Exception):
    """No usable access token: the account needs to sign in again."""


@dataclass
class Pkce:
    verifier: str
    challenge: str


def make_pkce() -> Pkce:
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return Pkce(verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii"))


def requested_scopes(provider: ProviderPreset, needed: list[str], existing: list[str] | None = None) -> list[str]:
    """What a sign-in asks for: the provider's base scopes, what the sync needs, and
    what the account already granted (so extending an account never narrows it)."""
    ordered: list[str] = []
    for scope in [*provider.base_scopes, *(existing or []), *needed]:
        normal = provider.normalize_scope(scope)
        if normal.lower() not in {s.lower() for s in ordered}:
            ordered.append(normal)
    return ordered


def loopback_redirect_uri(provider: ProviderPreset, port: int) -> str:
    return f"http://{provider.loopback_host}:{port}"


def begin_sign_in(session: Session, provider: ProviderPreset, scopes: list[str], port: int, return_to: str,
                  automation_slug: str | None = None) -> str:
    """Start a browser sign-in. Returns the provider URL to send the browser to."""
    pkce = make_pkce()
    state = secrets.token_urlsafe(24)
    redirect_uri = loopback_redirect_uri(provider, port)
    pending_sign_ins.purge_expired(session)
    pending_sign_ins.save(session, PendingSignIn(
        id=state, kind=KIND_BROWSER, provider=provider.key, scopes=list(scopes), automation_slug=automation_slug,
        return_to=return_to, expires_at=time.time() + PENDING_TTL_SECONDS, redirect_uri=redirect_uri,
    ), pkce.verifier)
    params = {
        "client_id": provider.client_id(),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": pkce.challenge,
        "code_challenge_method": "S256",
        **dict(provider.extra_authorize_params),
    }
    return f"{outgoing_url(provider.authorize_url)}?{urlencode(params)}"


def pending_sign_in(session: Session, state: str | None) -> PendingSignIn | None:
    """The browser sign-in this `state` names, if it's still pending."""
    pending = pending_sign_ins.load(session, state)
    return pending if pending is not None and pending.kind == KIND_BROWSER else None


@dataclass(frozen=True)
class SignedIn:
    connection_key: str
    automation_slug: str | None
    return_to: str


def _token_request(http: requests.Session, provider: ProviderPreset, data: dict[str, str]) -> dict:
    secret = provider.client_secret()
    payload = {"client_id": provider.client_id(), **data, **({"client_secret": secret} if secret else {})}
    try:
        response = http.post(outgoing_url(provider.token_url), data=payload, timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise SignInError("unreachable", str(e)) from e
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200:
        raise SignInError(str(body.get("error") or response.status_code), str(body.get("error_description", "")))
    if "access_token" not in body:
        raise SignInError("no_access_token")
    return body


def _token_secret(body: dict, previous: dict | None = None) -> dict:
    expires_in = int(body.get("expires_in") or 3600)
    return {
        "access_token": body["access_token"],
        # Providers that don't rotate refresh tokens omit it on refresh.
        "refresh_token": body.get("refresh_token") or (previous or {}).get("refresh_token"),
        "expires_at": int(time.time()) + expires_in,
        "token_type": body.get("token_type", "Bearer"),
    }


def _granted_scopes(provider: ProviderPreset, body: dict, requested: list[str]) -> list[str]:
    granted = body.get("scope")
    if not granted:
        return requested
    return sorted({provider.normalize_scope(s) for s in str(granted).split()} | set(provider.implicit_scopes))


def _whoami(http: requests.Session, provider: ProviderPreset, access_token: str) -> tuple[str, str | None]:
    """Ask the provider who signed in. Graph's /me sometimes answers a transient 5xx
    (seen: 504) right after a good sign-in, so server errors and timeouts are retried
    a few times before the sign-in is given up."""
    response = None
    for attempt in range(WHOAMI_ATTEMPTS):
        if attempt:
            time.sleep(WHOAMI_RETRY_SECONDS * attempt)
        try:
            response = http.get(outgoing_url(provider.whoami_url), timeout=HTTP_TIMEOUT_SECONDS,
                                headers={"Authorization": f"Bearer {access_token}"})
        except requests.RequestException as e:
            if attempt == WHOAMI_ATTEMPTS - 1:
                raise SignInError("unreachable", str(e)) from e
            continue
        if response.status_code < 500:
            break
    if response.status_code != 200:
        raise SignInError("whoami_failed", f"{provider.whoami_url} answered {response.status_code}")
    body = response.json()
    account_id = field_at(body, provider.account_id_field)
    if not account_id:
        raise SignInError("whoami_failed", "the provider didn't say which account signed in")
    label = next((str(v) for v in (field_at(body, f) for f in provider.account_label_fields) if v), None)
    return str(account_id), label


def _store_sign_in(session: Session, http: requests.Session, provider: ProviderPreset, body: dict,
                   requested: list[str]) -> str:
    secret = _token_secret(body)
    account_id, label = _whoami(http, provider, secret["access_token"])
    return connections.upsert_after_sign_in(session, provider.key, account_id, label,
                                            _granted_scopes(provider, body, requested), secret)


def resolve_provider(session: Session, key: str) -> ProviderPreset | None:
    # Imported here: adapters loads the Starlark handlers, which use this module.
    from yaffo.sync.adapters import provider_for_key

    return provider_for_key(session, key)


def finish_sign_in(session: Session, state: str, params: dict[str, str],
                   http: requests.Session | None = None) -> SignedIn:
    """Complete a browser sign-in from the return address's query string."""
    claimed = pending_sign_ins.claim(session, state)
    if claimed is None or claimed[0].kind != KIND_BROWSER:
        raise SignInError("unknown_state")
    pending, verifier = claimed
    if "error" in params:
        raise SignInError(params["error"], params.get("error_description", ""))
    code = params.get("code")
    if not code:
        raise SignInError("no_code")
    provider = resolve_provider(session, pending.provider)
    if provider is None:
        raise SignInError("unknown_provider")
    http = http or requests.Session()
    body = _token_request(http, provider, {
        "grant_type": "authorization_code", "code": code, "redirect_uri": pending.redirect_uri,
        "code_verifier": verifier,
    })
    key = _store_sign_in(session, http, provider, body, pending.scopes)
    return SignedIn(connection_key=key, automation_slug=pending.automation_slug, return_to=pending.return_to)


def begin_device_code(session: Session, provider: ProviderPreset, scopes: list[str],
                      automation_slug: str | None = None,
                      http: requests.Session | None = None) -> tuple[str, PendingSignIn]:
    """Start a device-code sign-in. Returns a handle for polling and what to show."""
    if not provider.device_code_url:
        raise SignInError("device_code_unsupported")
    http = http or requests.Session()
    try:
        response = http.post(outgoing_url(provider.device_code_url), timeout=HTTP_TIMEOUT_SECONDS,
                             data={"client_id": provider.client_id(), "scope": " ".join(scopes)})
    except requests.RequestException as e:
        raise SignInError("unreachable", str(e)) from e
    body = response.json() if response.content else {}
    if response.status_code != 200 or "device_code" not in body:
        raise SignInError(str(body.get("error") or response.status_code), str(body.get("error_description", "")))
    handle = secrets.token_urlsafe(16)
    pending = PendingSignIn(
        id=handle, kind=KIND_DEVICE, provider=provider.key, scopes=list(scopes), automation_slug=automation_slug,
        return_to="", expires_at=time.time() + int(body.get("expires_in") or 900),
        user_code=body["user_code"], verification_uri=body.get("verification_uri") or body.get("verification_url", ""),
        interval=int(body.get("interval") or 5),
    )
    pending_sign_ins.purge_expired(session)
    pending_sign_ins.save(session, pending, body["device_code"])
    return handle, pending


DEVICE_PENDING = "pending"
DEVICE_DONE = "done"


def poll_device_code(session: Session, handle: str,
                     http: requests.Session | None = None) -> tuple[str, SignedIn | None]:
    """One poll of a device-code sign-in: (DEVICE_PENDING, None) until the user has
    entered the code, then (DEVICE_DONE, SignedIn). Raises SignInError when it's
    refused or expired. Polls faster than the provider's interval are answered
    pending without a request."""
    pending = pending_sign_ins.load(session, handle)
    if pending is None or pending.kind != KIND_DEVICE:
        raise SignInError("expired_token")
    if time.time() < pending.next_poll:
        return DEVICE_PENDING, None
    provider = resolve_provider(session, pending.provider)
    if provider is None:
        raise SignInError("unknown_provider")
    device_code = pending_sign_ins.device_code(handle)
    if not device_code:
        pending_sign_ins.discard(session, handle)
        raise SignInError("expired_token")
    http = http or requests.Session()
    try:
        body = _token_request(http, provider, {"grant_type": DEVICE_GRANT, "device_code": device_code})
    except SignInError as e:
        if e.code in ("authorization_pending", "slow_down"):
            pending.interval += 5 if e.code == "slow_down" else 0
            pending.next_poll = time.time() + pending.interval
            pending_sign_ins.update(session, pending)
            return DEVICE_PENDING, None
        pending_sign_ins.discard(session, handle)
        raise
    # Taken once: a second poll that raced this one finds it gone.
    if pending_sign_ins.claim(session, handle) is None:
        raise SignInError("expired_token")
    key = _store_sign_in(session, http, provider, body, pending.scopes)
    return DEVICE_DONE, SignedIn(connection_key=key, automation_slug=pending.automation_slug, return_to="")


# ---- access tokens for runs ---------------------------------------------------

def _refresh(bind: Engine | Connection, key: str, provider: ProviderPreset, ref: str,
             http: requests.Session) -> dict:
    """Refresh under the connection's lease. A process that finds the lease taken
    waits for it and uses the token the holder stored."""
    if not connections.take_refresh_lease(bind, key):
        connections.wait_for_lease(bind, key)
        secret = secrets_store.load_secret(ref)
        if secret and secret.get("expires_at", 0) - time.time() > REFRESH_MARGIN_SECONDS / 2:
            return secret
        if not connections.take_refresh_lease(bind, key):
            raise TokenUnavailable("another process is still refreshing the token")
    try:
        secret = secrets_store.load_secret(ref) or {}
        refresh_token = secret.get("refresh_token")
        if not refresh_token:
            raise TokenUnavailable("no refresh token")
        try:
            body = _token_request(http, provider, {"grant_type": "refresh_token", "refresh_token": refresh_token})
        except SignInError as e:
            if e.code == "unreachable":
                raise TokenUnavailable(str(e)) from e
            connections.mark_needs_sign_in(bind, key)
            raise TokenUnavailable(f"refresh refused: {e.code}") from e
        fresh = _token_secret(body, secret)
        secrets_store.store_secret(ref, fresh)
        return fresh
    finally:
        connections.release_refresh_lease(bind, key)


class TokenSource:
    """Access tokens for one connection during a run (or a Test, or a picker call).
    `token()` refreshes when the cached token is close to expiring; `refresh()`
    forces it after a 401. Both may only be called at a page boundary (no write
    lock held by the run), because the lease is a database write."""

    def __init__(self, bind: Engine | Connection, key: str, provider: ProviderPreset,
                 http: requests.Session | None = None):
        self.bind = bind
        self.key = key
        self.provider = provider
        self.http = http or requests.Session()
        self._secret: dict[str, Any] | None = None

    def _ref(self) -> str:
        with Session(bind=self.bind) as session:
            connection = connections.get(session, self.key)
        if connection is None:
            raise TokenUnavailable("the connection doesn't exist")
        if connection.status != connections.CONNECTION_READY:
            raise TokenUnavailable("the connection needs to sign in again")
        return connection.secret_ref

    def token(self) -> str:
        if self._secret is None:
            self._secret = secrets_store.load_secret(self._ref())
            if not self._secret:
                connections.mark_needs_sign_in(self.bind, self.key)
                raise TokenUnavailable("no token in the keychain")
        if self._secret.get("expires_at", 0) - time.time() < REFRESH_MARGIN_SECONDS:
            self._secret = _refresh(self.bind, self.key, self.provider, self._ref(), self.http)
        return self._secret["access_token"]

    def cached(self) -> str | None:
        """The current token without refreshing (mid-page)."""
        return (self._secret or {}).get("access_token")

    def refresh(self) -> str:
        self._secret = _refresh(self.bind, self.key, self.provider, self._ref(), self.http)
        return self._secret["access_token"]
