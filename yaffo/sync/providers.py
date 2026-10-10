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
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from yaffo.sync import oauth_clients

AUTH_OAUTH2 = "oauth2"
ORIGIN_OVERRIDES_ENV = "YAFFO_SYNC_ORIGIN_OVERRIDES"


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    label: str
    auth_kind: str
    authorize_url: str
    token_url: str
    client_id: Callable[[], str]
    base_urls: tuple[str, ...]
    followup_hosts: tuple[str, ...]
    # Asked for on every sign-in, on top of what the syncs using the account need.
    base_scopes: tuple[str, ...]
    whoami_url: str
    # Field of the whoami response holding the stable account id, then the fields
    # tried in order for the label shown in the UI.
    account_id_field: str
    account_label_fields: tuple[str, ...]
    # Microsoft public clients accept http://localhost on any port; Google desktop
    # clients the 127.0.0.1 literal. The return address has no path.
    loopback_host: str
    client_secret: Callable[[], str | None] = lambda: None
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
        have = {self.normalize_scope(s).lower() for s in granted} | {s.lower() for s in self.implicit_scopes}
        return [scope for scope in needed if self.normalize_scope(scope).lower() not in have]


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

PROVIDERS: dict[str, ProviderPreset] = {ONEDRIVE.key: ONEDRIVE}


def get_provider(key: str) -> ProviderPreset | None:
    return PROVIDERS.get(key)


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
