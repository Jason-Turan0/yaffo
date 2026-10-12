"""OAuth 1.0a (RFC 5849): signing requests, and the browser sign-in.

Services such as Flickr and SmugMug still use it. Every request to the service is
signed with HMAC-SHA1 over its method, its URL and its parameters, keyed with the
user's own app secret (the "consumer secret", from the service builder's app
credentials) and the account's token secret. Nothing is ever refreshed: a token lasts
until the user revokes it, and a refused one means signing in again.

Sign-in has three legs:

1. Yaffo asks the service for a request token (signed with the app's credentials,
   naming the return address in `oauth_callback`).
2. The browser goes to the service's authorize page with that token (plus the
   service's own parameters, e.g. Flickr's `perms=write`). The user approves, and the
   service sends the browser back to Yaffo's loopback root with `oauth_token` and
   `oauth_verifier`.
3. Yaffo trades the request token and the verifier for the access token and its
   secret, makes the provider's `check` call (signed) to name the account, and stores
   the connection, the token pair in the keychain.

The pending sign-in is keyed by the request token: the service echoes it back, so the
return address needs no `state` and can be exactly the loopback root an app registers.
The request token's secret waits in the keychain with it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Any, Iterable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests
from sqlalchemy.orm import Session

from yaffo.sync import connections, pending_sign_ins
from yaffo.sync.oauth import HTTP_TIMEOUT_SECONDS, PENDING_TTL_SECONDS, SignedIn, SignInError, resolve_provider
from yaffo.sync.pending_sign_ins import PendingSignIn
from yaffo.sync.providers import ProviderPreset, field_at, outgoing_url

KIND_OAUTH1 = "oauth1"
SIGNATURE_METHOD = "HMAC-SHA1"


def _enc(value: Any) -> str:
    """RFC 3986 percent-encoding, as RFC 5849 3.6 requires (only unreserved kept)."""
    return quote(str(value), safe="-._~")


def _base_uri(url: str) -> str:
    parts = urlsplit(url)
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    port = parts.port
    netloc = host if port is None or (scheme, port) in (("http", 80), ("https", 443)) else f"{host}:{port}"
    # The path as it goes on the wire: characters a URL can't carry percent-encoded,
    # escapes already there kept (requests sends it the same way).
    path = quote(parts.path or "/", safe="/%-._~!$&'()*+,;=:@")
    return urlunsplit((scheme, netloc, path, "", ""))


def signature_base(method: str, url: str, params: Iterable[tuple[str, str]]) -> str:
    """RFC 5849 3.4.1: the method, the base URI and the normalized parameters (the
    URL's query, the oauth_* values, and any signed body fields)."""
    query = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    pairs = sorted((_enc(k), _enc(v)) for k, v in [*query, *params])
    normalized = "&".join(f"{k}={v}" for k, v in pairs)
    return "&".join((method.upper(), _enc(_base_uri(url)), _enc(normalized)))


def sign(method: str, url: str, consumer_key: str, consumer_secret: str, token: str | None = None,
         token_secret: str | None = None, body: Iterable[tuple[str, str]] = (), extra: dict[str, str] | None = None,
         nonce: str | None = None, timestamp: str | None = None) -> str:
    """The Authorization header for one request. `body`: form fields that are part of
    the signature; `extra`: further oauth_* values (oauth_callback, oauth_verifier)."""
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": SIGNATURE_METHOD,
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_version": "1.0",
        **({"oauth_token": token} if token else {}),
        **(extra or {}),
    }
    base = signature_base(method, url, [*oauth.items(), *body])
    key = f"{_enc(consumer_secret)}&{_enc(token_secret or '')}"
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{_enc(k)}="{_enc(v)}"' for k, v in oauth.items())


def _app(provider: ProviderPreset) -> tuple[str, str]:
    key, secret = provider.client_id(), provider.client_secret()
    if not key or not secret:
        raise SignInError("app_missing", "the service's app key and secret aren't saved")
    return key, secret


def _token_call(http: requests.Session, url: str, header: str) -> dict[str, str]:
    try:
        response = http.post(outgoing_url(url), headers={"Authorization": header}, timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise SignInError("unreachable", str(e)) from e
    if response.status_code != 200:
        raise SignInError("token_refused", f"{url} answered {response.status_code}: {response.text[:200]}")
    values = dict(parse_qsl(response.text))
    if not values.get("oauth_token") or not values.get("oauth_token_secret"):
        raise SignInError("token_refused", "the service didn't send a token")
    return values


def begin_sign_in(session: Session, provider: ProviderPreset, port: int, return_to: str,
                  automation_slug: str | None = None, http: requests.Session | None = None) -> str:
    """Get a request token and return the authorize page to send the browser to."""
    http = http or requests.Session()
    consumer_key, consumer_secret = _app(provider)
    callback = f"http://{provider.loopback_host}:{port}"
    values = _token_call(http, provider.request_token_url,
                         sign("POST", provider.request_token_url, consumer_key, consumer_secret,
                              extra={"oauth_callback": callback}))
    if values.get("oauth_callback_confirmed", "true") != "true":
        raise SignInError("token_refused", "the service didn't confirm the return address")
    pending_sign_ins.purge_expired(session)
    pending_sign_ins.save(session, PendingSignIn(
        id=values["oauth_token"], kind=KIND_OAUTH1, provider=provider.key, scopes=[],
        automation_slug=automation_slug, return_to=return_to, expires_at=time.time() + PENDING_TTL_SECONDS,
        redirect_uri=callback), values["oauth_token_secret"])
    params = {"oauth_token": values["oauth_token"], **dict(provider.extra_authorize_params)}
    return f"{outgoing_url(provider.authorize_url)}?{urlencode(params)}"


def pending_sign_in(session: Session, request_token: str | None) -> PendingSignIn | None:
    pending = pending_sign_ins.load(session, request_token)
    return pending if pending is not None and pending.kind == KIND_OAUTH1 else None


def finish_sign_in(session: Session, params: dict[str, str], http: requests.Session | None = None) -> SignedIn:
    """Complete the sign-in from the return address's `oauth_token` / `oauth_verifier`."""
    claimed = pending_sign_ins.claim(session, params.get("oauth_token") or "")
    if claimed is None or claimed[0].kind != KIND_OAUTH1:
        raise SignInError("unknown_state")
    pending, request_secret = claimed
    if not params.get("oauth_verifier"):
        raise SignInError("no_code", "the service didn't send a verifier (the user may have declined)")
    provider = resolve_provider(session, pending.provider)
    if provider is None:
        raise SignInError("unknown_provider")
    http = http or requests.Session()
    consumer_key, consumer_secret = _app(provider)
    values = _token_call(http, provider.access_token_url,
                         sign("POST", provider.access_token_url, consumer_key, consumer_secret,
                              token=pending.id, token_secret=request_secret,
                              extra={"oauth_verifier": params["oauth_verifier"]}))
    secret = {"token": values["oauth_token"], "token_secret": values["oauth_token_secret"]}
    account_id, label = _whoami(http, provider, consumer_key, consumer_secret, secret)
    key = connections.upsert_after_sign_in(session, provider.key, account_id, label, [], secret)
    return SignedIn(connection_key=key, automation_slug=pending.automation_slug, return_to=pending.return_to)


def _whoami(http: requests.Session, provider: ProviderPreset, consumer_key: str, consumer_secret: str,
            secret: dict[str, str]) -> tuple[str, str | None]:
    header = sign("GET", provider.whoami_url, consumer_key, consumer_secret, secret["token"], secret["token_secret"])
    try:
        response = http.get(outgoing_url(provider.whoami_url), headers={"Authorization": header},
                            timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise SignInError("unreachable", str(e)) from e
    if response.status_code != 200:
        raise SignInError("whoami_failed", f"the check call answered {response.status_code}")
    try:
        body = response.json()
    except ValueError as e:
        raise SignInError("whoami_failed", "the check call didn't answer JSON") from e
    account_id = field_at(body, provider.account_id_field)
    if not account_id:
        raise SignInError("whoami_failed", "the service didn't say which account signed in")
    label = next((str(v) for v in (field_at(body, f) for f in provider.account_label_fields) if v), None)
    return str(account_id), label
