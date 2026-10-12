"""Provider presets: how to sign in to a service and which hosts may be called.

A provider carries no sync logic. Adapters name the provider they need
(`ADAPTER["provider"]`); a connection is a signed-in account at one. Everything a
connection row doesn't store comes from here: the OAuth endpoints and client id,
`base_urls` (hosts calls go to, with the account's credentials) and
`followup_hosts` (hosts that only receive URLs a granted call returned, such as
OneDrive's pre-authenticated download links; never sent credentials), the scopes
every sign-in asks for, and the `whoami` call that names the account.

`YAFFO_SYNC_ORIGIN_OVERRIDES` (tests and the UI-test sandbox only) maps real
origins to a local fake, e.g. {"https://graph.microsoft.com": "http://127.0.0.1:5098"}.
It's applied where a request leaves Yaffo (and to the sign-in page the browser
opens), after every check, so grants, host rules and adapters see the real URLs.

Sign-in kinds: `oauth2` (browser or device code), `oauth1a` (browser; every request is
signed with the app's and the account's secrets, see oauth1.py), `api_key` (a key the user pastes,
sent in a header, a query parameter, or a path placeholder) and `basic` (a username
and an app password). A **self-hosted** provider (`instance`) has no fixed host: its
`base_urls` is `{base_url}`, the server address the user enters at sign-in, stored
on the connection. `bind_instance` gives the provider as one connection sees it, with
that address in place, so every host rule below works unchanged. Plain http is only
ever allowed for such an address, and only when it's on a private network.
"""
from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, field, replace
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from yaffo.sync import oauth_clients

AUTH_OAUTH2 = "oauth2"
AUTH_OAUTH1 = "oauth1a"
AUTH_API_KEY = "api_key"
AUTH_BASIC = "basic"
ORIGIN_OVERRIDES_ENV = "YAFFO_SYNC_ORIGIN_OVERRIDES"

# Connection placeholders: filled in by the host from the connection, never by an
# adapter. `{base_url}` is a self-hosted server's address; `{secret}` is the secret
# part of a key placed in the path (a share link's token).
BASE_URL_PLACEHOLDER = "{base_url}"
SECRET_PLACEHOLDER = "{secret}"
CONNECTION_PLACEHOLDERS = ("base_url", "secret")

KEY_IN_HEADER = "header"
KEY_IN_QUERY = "query"
KEY_IN_PATH = "path"

# Host names a home network uses for its own machines.
PRIVATE_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal")


@dataclass(frozen=True)
class ApiKeyPlacement:
    """Where an `api_key` connection's key goes in every request to its hosts."""
    kind: str                 # header | query | path
    name: str = ""            # the header or query parameter; unused for path
    prefix: str = ""          # put before the key in a header ("Bearer ")


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    label: str
    auth_kind: str
    base_urls: tuple[str, ...]
    followup_hosts: tuple[str, ...]
    # The read-only `check` call made right after sign-in (`whoami`): it proves the
    # credential works and names the account. May start with {base_url}.
    whoami_url: str
    # Field of the whoami response holding the stable account id, then the fields
    # tried in order for the label shown in the UI.
    account_id_field: str
    account_label_fields: tuple[str, ...]
    # OAuth 2 only.
    authorize_url: str = ""
    token_url: str = ""
    client_id: Callable[[], str] = lambda: ""
    # Asked for on every sign-in, on top of what the syncs using the account need.
    base_scopes: tuple[str, ...] = ()
    # Microsoft public clients accept http://localhost on any port; Google desktop
    # clients the 127.0.0.1 literal. The return address has no path.
    loopback_host: str = "127.0.0.1"
    client_secret: Callable[[], str | None] = lambda: None
    # oauth1a only: the temporary-credentials and token URLs (authorize_url is shared),
    # and whether a multipart upload's form fields are part of the signature (Flickr
    # signs every field but the file; RFC 5849 signs only url-encoded bodies).
    request_token_url: str = ""
    access_token_url: str = ""
    sign_multipart_fields: bool = False
    # api_key only.
    api_key: ApiKeyPlacement | None = None
    # A self-hosted service: the user enters its address at sign-in.
    instance: bool = False
    # Words for the sign-in form: what the key is called, and where to find it.
    key_label: str = "API key"
    key_help: str = ""
    device_code_url: str | None = None
    revoke_url: str | None = None
    extra_authorize_params: tuple[tuple[str, str], ...] = ()
    # Scope prefixes the token endpoint adds that the app never writes.
    scope_prefixes: tuple[str, ...] = ()
    # Scopes that are only requested, never reported back as granted.
    implicit_scopes: tuple[str, ...] = field(default=())

    def normalize_scope(self, scope: str) -> str:
        for prefix in self.scope_prefixes:
            if scope.lower().startswith(prefix.lower()):
                scope = scope[len(prefix):]
        return scope

    def missing_scopes(self, granted: list[str], needed: list[str]) -> list[str]:
        if self.auth_kind != AUTH_OAUTH2:
            return []  # a key, a password or an OAuth 1 token can do what it was granted at sign-in
        have = {self.normalize_scope(s).lower() for s in granted} | {s.lower() for s in self.implicit_scopes}
        return [scope for scope in needed if self.normalize_scope(scope).lower() not in have]

    @property
    def is_oauth(self) -> bool:
        """Signs in through the service's own page in the browser (OAuth 1 or 2)."""
        return self.auth_kind in (AUTH_OAUTH2, AUTH_OAUTH1)

    @property
    def is_oauth2(self) -> bool:
        return self.auth_kind == AUTH_OAUTH2


ONEDRIVE = ProviderPreset(
    key="onedrive",
    label="OneDrive",
    auth_kind=AUTH_OAUTH2,
    authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    device_code_url="https://login.microsoftonline.com/common/oauth2/v2.0/devicecode",
    client_id=oauth_clients.microsoft_client_id,
    base_urls=("https://graph.microsoft.com",),
    # Where Graph's /content redirects: pre-authenticated download URLs. Personal
    # accounts download from my.microsoftpersonalcontent.com (seen 2026-10-10).
    followup_hosts=("*.1drv.com", "*.sharepoint.com", "*.livefilestore.com", "*.onedrive.com",
                    "*.microsoftpersonalcontent.com"),
    base_scopes=("offline_access", "User.Read"),
    whoami_url="https://graph.microsoft.com/v1.0/me",
    account_id_field="id",
    account_label_fields=("mail", "userPrincipalName", "displayName"),
    loopback_host="localhost",
    extra_authorize_params=(("prompt", "select_account"),),
    scope_prefixes=("https://graph.microsoft.com/",),
    implicit_scopes=("offline_access",),
)

GOOGLE_PHOTOS = ProviderPreset(
    key="google_photos",
    label="Google Photos",
    auth_kind=AUTH_OAUTH2,
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    client_id=oauth_clients.google_client_id,
    client_secret=oauth_clients.google_client_secret,
    base_urls=("https://photoslibrary.googleapis.com",),
    followup_hosts=(),
    base_scopes=("openid", "email"),
    whoami_url="https://openidconnect.googleapis.com/v1/userinfo",
    account_id_field="sub",
    account_label_fields=("email", "name"),
    loopback_host="127.0.0.1",
    extra_authorize_params=(("access_type", "offline"), ("prompt", "consent")),
)

IMMICH = ProviderPreset(
    key="immich",
    label="Immich",
    auth_kind=AUTH_API_KEY,
    api_key=ApiKeyPlacement(KEY_IN_HEADER, "x-api-key"),
    instance=True,
    base_urls=(BASE_URL_PLACEHOLDER,),
    followup_hosts=(),
    whoami_url=BASE_URL_PLACEHOLDER + "/api/users/me",
    account_id_field="id",
    account_label_fields=("email", "name"),
    key_help="In Immich, open Account Settings › API Keys and create a key.",
)

PROVIDERS: dict[str, ProviderPreset] = {p.key: p for p in (ONEDRIVE, GOOGLE_PHOTOS, IMMICH)}


def get_provider(key: str) -> ProviderPreset | None:
    return PROVIDERS.get(key)


def is_private_host(host: str | None) -> bool:
    """A home network's own machine: a private, loopback or link-local address, or
    a name under a home-network suffix (.local, .lan, ...)."""
    if not host:
        return False
    host = host.lower().strip("[]")
    if host == "localhost" or host.endswith(PRIVATE_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


class InstanceUrlError(ValueError):
    """A server address that can't be used. `code` is stable (shown translated)."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def normalize_instance_url(text: str, allow_http: bool = False) -> str:
    """A self-hosted server's address as stored: scheme and host (and port), no path.
    http is refused for a public address, and for a private one unless the user has
    accepted the warning (`allow_http`). An address typed without a scheme is https."""
    text = (text or "").strip()
    if text and "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise InstanceUrlError("instance_invalid", f"{text!r} isn't a server address")
    if parts.path.strip("/") or parts.query or parts.fragment:
        raise InstanceUrlError("instance_has_path", "enter only the server's address, without a path")
    if parts.scheme == "http":
        if not is_private_host(parts.hostname):
            raise InstanceUrlError("instance_http_public", "http is only allowed on a home network")
        if not allow_http:
            raise InstanceUrlError("instance_http_unconfirmed", "confirm the http warning first")
    return f"{parts.scheme}://{parts.netloc}".lower()


def bind_instance(provider: ProviderPreset, instance: dict | None) -> ProviderPreset:
    """The provider as one connection sees it: a self-hosted provider's {base_url}
    replaced by the connection's server address. Unchanged for other providers."""
    if not provider.instance:
        return provider
    base_url = (instance or {}).get("base_url")
    if not base_url:
        return provider
    return replace(provider,
                   base_urls=tuple(b.replace(BASE_URL_PLACEHOLDER, base_url) for b in provider.base_urls),
                   whoami_url=provider.whoami_url.replace(BASE_URL_PLACEHOLDER, base_url))


def instance_host(instance: dict | None) -> str | None:
    """The server's host (and port), as the review names it."""
    base_url = (instance or {}).get("base_url")
    return urlsplit(base_url).netloc if base_url else None


def host_matches(host: str, pattern_host: str) -> bool:
    host, pattern_host = host.lower(), pattern_host.lower()
    if pattern_host.startswith("*."):
        suffix = pattern_host[1:]
        return host.endswith(suffix) and len(host) > len(suffix)
    return host == pattern_host


def is_base_url(provider: ProviderPreset, url: str) -> bool:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    return any(origin == base.lower() for base in provider.base_urls)


def is_followup_host(provider: ProviderPreset, url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname is not None and any(
        host_matches(parts.hostname, pattern) for pattern in provider.followup_hosts)


def allowed_url(provider: ProviderPreset, url: str) -> bool:
    return is_base_url(provider, url) or is_followup_host(provider, url)


def _overrides() -> dict[str, str]:
    raw = os.environ.get(ORIGIN_OVERRIDES_ENV)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return {str(k).rstrip("/").lower(): str(v).rstrip("/") for k, v in data.items()} if isinstance(data, dict) else {}


def outgoing_url(url: str) -> str:
    """The URL actually requested: the same unless an origin override applies."""
    overrides = _overrides()
    if not overrides:
        return url
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    target = overrides.get(origin)
    if target is None:
        return url
    new = urlsplit(target)
    return urlunsplit((new.scheme, new.netloc, parts.path, parts.query, parts.fragment))


def incoming_url(url: str) -> str:
    """The inverse of outgoing_url, for redirect locations a fake returns."""
    overrides = _overrides()
    if not overrides:
        return url
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    for real, fake in overrides.items():
        if origin == fake.lower():
            real_parts = urlsplit(real)
            return urlunsplit((real_parts.scheme, real_parts.netloc, parts.path, parts.query, parts.fragment))
    return url


# ---- custom adapters: the provider from the adapter's own connection block ---------------

CUSTOM_PREFIX = "custom:"
CUSTOM_AUTH_KINDS = (AUTH_OAUTH2, AUTH_OAUTH1, AUTH_API_KEY, AUTH_BASIC)
# Declared in the design, built when three real services need them (rule of three).
LATER_AUTH_KINDS = ("sigv4", "session")
REDIRECT_KINDS = ("registered", "any_loopback", "per_request", "device_code")
OAUTH1_REDIRECT_KINDS = ("registered", "per_request")
_BLOCK_KEYS = {"auth_kind", "label", "base_urls", "instance", "followup_hosts", "check", "api_key", "key_label",
               "key_help", "auth", "redirect", "app_credentials", "app_form_labels"}


class ConnectionBlockError(ValueError):
    """A custom adapter's connection block breaks a rule; the message says which."""


def _https_origin(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise ConnectionBlockError(f"{what} must be text")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.path.strip("/") or parts.query or "{" in value:
        raise ConnectionBlockError(f"{what} {value!r} must be a plain https origin, like https://api.example.com")
    return f"{parts.scheme}://{parts.netloc}".lower()


def _https_url(value: object, what: str) -> str:
    if not isinstance(value, str) or urlsplit(value).scheme != "https" or not urlsplit(value).hostname:
        raise ConnectionBlockError(f"{what} must be an https URL")
    return value


def provider_from_block(key: str, label: str, block: object,
                        app_credentials: Callable[[], dict] | None = None) -> ProviderPreset:
    """The provider a custom adapter declares in ADAPTER["connection"]. Raises
    ConnectionBlockError naming the first rule it breaks. `app_credentials` returns the
    user's own OAuth app (client id and secret), kept in the keychain under the adapter."""
    if not isinstance(block, dict):
        raise ConnectionBlockError("connection must be a dict")
    unknown = sorted(set(block) - _BLOCK_KEYS)
    if unknown:
        raise ConnectionBlockError(f"connection has unknown keys {unknown}")
    kind = block.get("auth_kind")
    if kind in LATER_AUTH_KINDS:
        raise ConnectionBlockError(f"auth_kind {kind!r} isn't supported yet")
    if kind not in CUSTOM_AUTH_KINDS:
        raise ConnectionBlockError(f"auth_kind must be one of {list(CUSTOM_AUTH_KINDS)}")
    instance = block.get("instance")
    if instance not in (None, ["base_url"]):
        raise ConnectionBlockError('instance must be ["base_url"]')
    if instance:
        if block.get("base_urls") not in (None, []):
            raise ConnectionBlockError("a self-hosted service has no fixed base_urls")
        base_urls: tuple[str, ...] = (BASE_URL_PLACEHOLDER,)
    else:
        raw = block.get("base_urls")
        if not isinstance(raw, list) or not raw:
            raise ConnectionBlockError("base_urls must list the service's https origins")
        base_urls = tuple(_https_origin(u, "base_urls entry") for u in raw)
    followups = block.get("followup_hosts") or []
    if not isinstance(followups, list) or not all(
            isinstance(h, str) and h and "/" not in h and ("*" not in h or (h.startswith("*.") and h.count("*") == 1))
            for h in followups):
        raise ConnectionBlockError("followup_hosts must be host names (a leading *. allowed)")
    check = block.get("check")
    if not isinstance(check, dict) or check.get("method", "GET") != "GET" or not isinstance(check.get("url"), str):
        raise ConnectionBlockError('check must be {"method": "GET", "url": ...}, the read-only call naming the account')
    check_url = check["url"]
    if instance:
        if not check_url.startswith(BASE_URL_PLACEHOLDER + "/"):
            raise ConnectionBlockError("a self-hosted service's check url starts with {base_url}/")
    elif not any(_https_origin(f"{urlsplit(check_url).scheme}://{urlsplit(check_url).netloc}", "check url") == b
                 for b in base_urls):
        raise ConnectionBlockError("the check url must be on base_urls")
    labels = check.get("account_label") or []
    if isinstance(labels, str):
        labels = [labels]
    common = dict(key=key, label=str(block.get("label") or label), base_urls=base_urls,
                  followup_hosts=tuple(followups), whoami_url=check_url,
                  account_id_field=str(check.get("account_id") or "id"),
                  account_label_fields=tuple(str(f) for f in labels), instance=bool(instance),
                  key_label=str(block.get("key_label") or "API key"), key_help=str(block.get("key_help") or ""))
    if kind == AUTH_API_KEY:
        placement = block.get("api_key")
        if not isinstance(placement, dict) or placement.get("in") not in (KEY_IN_HEADER, KEY_IN_QUERY, KEY_IN_PATH):
            raise ConnectionBlockError('api_key must say where the key goes: {"in": "header" | "query" | "path"}')
        name = placement.get("name") or ""
        if placement["in"] != KEY_IN_PATH and not (isinstance(name, str) and name.replace("-", "").replace("_", "").isalnum()):
            raise ConnectionBlockError("api_key needs the header or query parameter name")
        if placement["in"] == KEY_IN_HEADER and name.lower() in ("cookie", "host"):
            raise ConnectionBlockError(f"the key can't go in the {name} header")
        return ProviderPreset(auth_kind=AUTH_API_KEY, **common,
                              api_key=ApiKeyPlacement(placement["in"], name, str(placement.get("prefix") or "")))
    if kind == AUTH_BASIC:
        return ProviderPreset(auth_kind=AUTH_BASIC, **common)
    auth = block.get("auth")
    credentials_of = app_credentials or (lambda: {})
    if kind == AUTH_OAUTH1:
        if not isinstance(auth, dict):
            raise ConnectionBlockError("an oauth1a connection needs auth: {request_token_url, authorize_url, "
                                       "access_token_url}")
        if block.get("redirect") not in OAUTH1_REDIRECT_KINDS:
            raise ConnectionBlockError(f"an oauth1a redirect must be one of {list(OAUTH1_REDIRECT_KINDS)}")
        params = auth.get("authorize_params") or {}
        if not isinstance(params, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in params.items()):
            raise ConnectionBlockError("auth.authorize_params must map names to text (e.g. {\"perms\": \"write\"})")
        return ProviderPreset(auth_kind=AUTH_OAUTH1, **common,
                              request_token_url=_https_url(auth.get("request_token_url"), "auth.request_token_url"),
                              authorize_url=_https_url(auth.get("authorize_url"), "auth.authorize_url"),
                              access_token_url=_https_url(auth.get("access_token_url"), "auth.access_token_url"),
                              extra_authorize_params=tuple(sorted(params.items())),
                              sign_multipart_fields=bool(auth.get("sign_multipart_fields")),
                              client_id=lambda: str(credentials_of().get("client_id") or ""),
                              client_secret=lambda: credentials_of().get("client_secret"))
    if not isinstance(auth, dict):
        raise ConnectionBlockError("an oauth2 connection needs auth: {authorize_url, token_url, scopes}")
    if block.get("redirect") not in REDIRECT_KINDS:
        raise ConnectionBlockError(f"redirect must be one of {list(REDIRECT_KINDS)}")
    scopes = auth.get("scopes") or []
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        raise ConnectionBlockError("auth.scopes must be a list of text")
    device = auth.get("device_code_url")
    return ProviderPreset(auth_kind=AUTH_OAUTH2, **common,
                          authorize_url=_https_url(auth.get("authorize_url"), "auth.authorize_url"),
                          token_url=_https_url(auth.get("token_url"), "auth.token_url"),
                          device_code_url=_https_url(device, "auth.device_code_url") if device else None,
                          base_scopes=tuple(scopes),
                          client_id=lambda: str(credentials_of().get("client_id") or ""),
                          client_secret=lambda: credentials_of().get("client_secret"))


def field_at(body: object, path: str) -> object:
    """A field of a `check` response by dotted path ("user.username._content"), or
    None when any step is missing."""
    value = body
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value
