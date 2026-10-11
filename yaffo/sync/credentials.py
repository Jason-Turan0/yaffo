"""How a connection's secret goes into a request, per sign-in kind, and signing in
with a key or a password.

A WebClient asks its Credentials for what to add to a request bound for one of the
connection's `base_urls` (never anything else): an Authorization header for OAuth 2
and `basic`, the key's header or query parameter for `api_key`, or the secret part
of a path (`{secret}`) for a key placed in the URL. Scripts never see any of it.

OAuth 2 tokens expire and are refreshed (oauth.TokenSource). A key or a password
doesn't expire: when the service refuses it, refreshing can't help, so the run marks
the connection `needs_sign_in` and the user enters it again.

`sign_in_with_secret` is the Account step for `api_key` and `basic`: Yaffo makes the
provider's `check` call with what the user entered (on a self-hosted service, at the
address they entered) and stores the connection only if it works.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Any

import requests
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from yaffo.logging_config import get_logger
from yaffo.sync import connections, oauth1, secrets_store
from yaffo.sync.oauth import HTTP_TIMEOUT_SECONDS, SignInError, TokenSource, TokenUnavailable
from yaffo.sync.providers import (
    AUTH_API_KEY, AUTH_BASIC, AUTH_OAUTH1, KEY_IN_HEADER, KEY_IN_PATH, KEY_IN_QUERY, ProviderPreset, bind_instance, field_at,
    outgoing_url,
)

logger = get_logger(__name__)

MAX_SECRET_LENGTH = 4096


class Credentials:
    """What a request to one of the connection's hosts carries."""

    def headers(self, mid_page: bool) -> dict[str, str]:
        return {}

    # Credentials that sign each request (OAuth 1) are computed per request, from its
    # method, URL and signed form fields, and again on every redirect hop.
    per_request = False

    def request_headers(self, method: str, url: str, form: Any = (), mid_page: bool = False) -> dict[str, str]:
        return self.headers(mid_page)

    def header_names(self) -> set[str]:
        """The (lowercase) headers that carry the secret: never sent off the base URLs."""
        return {"authorization"}

    def query(self) -> dict[str, str]:
        return {}

    def path_secret(self) -> str | None:
        return None

    @property
    def can_refresh(self) -> bool:
        return False

    def refresh(self) -> None:
        raise TokenUnavailable("this kind of sign-in can't be refreshed")


class OAuthCredentials(Credentials):
    def __init__(self, tokens: TokenSource):
        self.tokens = tokens

    def headers(self, mid_page: bool) -> dict[str, str]:
        token = self.tokens.cached() if mid_page and self.tokens.cached() else self.tokens.token()
        return {"Authorization": f"Bearer {token}"}

    @property
    def can_refresh(self) -> bool:
        return True

    def refresh(self) -> None:
        self.tokens.refresh()


class StoredSecret:
    """A key or password read once from the keychain. A missing entry marks the
    connection `needs_sign_in`."""

    def __init__(self, bind: Engine | Connection, key: str):
        self.bind = bind
        self.key = key
        self._secret: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        if self._secret is None:
            with Session(bind=self.bind) as session:
                connection = connections.get(session, self.key)
            if connection is None:
                raise TokenUnavailable("the connection doesn't exist")
            if connection.status != connections.CONNECTION_READY:
                raise TokenUnavailable("the connection needs to sign in again")
            secret = secrets_store.load_secret(connection.secret_ref)
            if not secret:
                connections.mark_needs_sign_in(self.bind, self.key)
                raise TokenUnavailable("no secret in the keychain")
            self._secret = secret
        return self._secret


class ApiKeyCredentials(Credentials):
    def __init__(self, provider: ProviderPreset, secret: StoredSecret | dict[str, Any]):
        assert provider.api_key is not None
        self.placement = provider.api_key
        self._secret = secret

    def _key(self) -> str:
        secret = self._secret.load() if isinstance(self._secret, StoredSecret) else self._secret
        return str(secret.get("api_key") or "")

    def headers(self, mid_page: bool) -> dict[str, str]:
        if self.placement.kind != KEY_IN_HEADER:
            return {}
        return {self.placement.name: f"{self.placement.prefix}{self._key()}"}

    def header_names(self) -> set[str]:
        return {"authorization", self.placement.name.lower()} if self.placement.kind == KEY_IN_HEADER \
            else {"authorization"}

    def query(self) -> dict[str, str]:
        return {self.placement.name: self._key()} if self.placement.kind == KEY_IN_QUERY else {}

    def path_secret(self) -> str | None:
        return self._key() if self.placement.kind == KEY_IN_PATH else None


class BasicCredentials(Credentials):
    def __init__(self, secret: StoredSecret | dict[str, Any]):
        self._secret = secret

    def headers(self, mid_page: bool) -> dict[str, str]:
        secret = self._secret.load() if isinstance(self._secret, StoredSecret) else self._secret
        pair = f"{secret.get('username', '')}:{secret.get('password', '')}".encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(pair).decode("ascii")}


class OAuth1Credentials(Credentials):
    """OAuth 1.0a: every request signed with the app's secret and the account's token
    secret (oauth1.py). The token doesn't expire; a refused one needs a new sign-in."""

    per_request = True

    def __init__(self, provider: ProviderPreset, secret: StoredSecret | dict[str, Any]):
        self.provider = provider
        self._secret = secret

    def request_headers(self, method: str, url: str, form: Any = (), mid_page: bool = False) -> dict[str, str]:
        secret = self._secret.load() if isinstance(self._secret, StoredSecret) else self._secret
        consumer_key, consumer_secret = self.provider.client_id(), self.provider.client_secret()
        if not consumer_key or not consumer_secret:
            raise TokenUnavailable("the service's app key and secret aren't saved")
        return {"Authorization": oauth1.sign(method, url, consumer_key, consumer_secret, secret.get("token"),
                                             secret.get("token_secret"), body=list(form))}


def for_connection(bind: Engine | Connection, key: str, provider: ProviderPreset,
                   http: requests.Session) -> Credentials:
    """The credentials a run (or a Test, or a picker) uses for connection `key`."""
    if provider.auth_kind == AUTH_API_KEY:
        return ApiKeyCredentials(provider, StoredSecret(bind, key))
    if provider.auth_kind == AUTH_BASIC:
        return BasicCredentials(StoredSecret(bind, key))
    if provider.auth_kind == AUTH_OAUTH1:
        return OAuth1Credentials(provider, StoredSecret(bind, key))
    return OAuthCredentials(TokenSource(bind, key, provider, http=http))


# ---- signing in with a key or a password ------------------------------------------


def _clean(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SignInError(f"{field}_missing")
    if len(text) > MAX_SECRET_LENGTH or any(ch in text for ch in "\r\n"):
        raise SignInError(f"{field}_invalid")
    return text


def secret_from_form(provider: ProviderPreset, form: dict[str, Any]) -> dict[str, str]:
    """What the sign-in form entered, as the keychain stores it."""
    if provider.auth_kind == AUTH_API_KEY:
        return {"api_key": _clean(form.get("api_key"), "api_key")}
    if provider.auth_kind == AUTH_BASIC:
        return {"username": _clean(form.get("username"), "username"),
                "password": _clean(form.get("password"), "password")}
    raise SignInError("not_supported", f"{provider.auth_kind} signs in through the browser")


def _check(http: requests.Session, provider: ProviderPreset, credentials: Credentials) -> tuple[str, str | None]:
    """The provider's `check` call with the entered credentials: who signed in."""
    url = provider.whoami_url
    secret_path = credentials.path_secret()
    if secret_path is not None:
        url = url.replace("{secret}", secret_path)
    try:
        response = http.get(outgoing_url(url), headers=credentials.headers(mid_page=False),
                            params=credentials.query() or None, timeout=HTTP_TIMEOUT_SECONDS,
                            allow_redirects=False)
    except requests.exceptions.SSLError as e:
        # Often a home server that only speaks http, asked for https.
        raise SignInError("tls_failed", str(e)) from e
    except requests.RequestException as e:
        raise SignInError("unreachable", str(e)) from e
    if response.status_code in (401, 403):
        raise SignInError("rejected", f"the check call answered {response.status_code}")
    if response.status_code != 200:
        raise SignInError("whoami_failed", f"the check call answered {response.status_code}")
    try:
        body = response.json()
    except ValueError as e:
        raise SignInError("whoami_failed", "the check call didn't answer JSON") from e
    account_id = field_at(body, provider.account_id_field)
    label = next((str(v) for v in (field_at(body, f) for f in provider.account_label_fields) if v), None)
    if not account_id:
        # No usable id: the account is the key itself, as a fingerprint.
        account_id = "k" + hashlib.sha256(repr(sorted(credentials.headers(False).items())).encode()).hexdigest()[:24]
    return str(account_id), label


def sign_in_with_secret(session: Session, provider: ProviderPreset, secret: dict[str, str],
                        instance: dict[str, str] | None, http: requests.Session) -> str:
    """Check the entered key or password (at the entered server) and store the
    connection. A self-hosted account is named per server, so the same user on two
    servers is two connections. Returns the connection key. Commits."""
    bound = bind_instance(provider, instance)
    if provider.instance and bound is provider:
        raise SignInError("instance_missing")
    credentials: Credentials = (ApiKeyCredentials(bound, secret) if provider.auth_kind == AUTH_API_KEY
                                else BasicCredentials(secret))
    account_id, label = _check(http, bound, credentials)
    if provider.instance:
        host = (instance or {})["base_url"].split("://", 1)[-1]
        account_id = f"{account_id}@{host}"
        label = f"{label or provider.label} · {host}"
    return connections.upsert_after_sign_in(session, provider.key, account_id, label, [], secret,
                                            instance=dict(instance or {}))
