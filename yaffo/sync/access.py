"""A sync's web access: whether its account can be used, and the WebClient its
Starlark adapter calls through.

`make_http` is the requests session factory; tests replace it with a fake transport.
"""
from __future__ import annotations

from typing import Callable

import requests
from sqlalchemy.orm import Session

from yaffo.sync import connections, credentials
from yaffo.sync.adapters import LoadedAdapter
from yaffo.sync.providers import bind_instance, instance_host
from yaffo.sync.schemas import CONNECTION_NEEDS_SIGN_IN, SyncConfig
from yaffo.sync.starlark_adapter import StarlarkSourceHandler, StarlarkTargetHandler
from yaffo.sync.web import WebClient

make_http: Callable[[], requests.Session] = requests.Session

ACCOUNT_MISSING = "account_missing"
ACCOUNT_NEEDS_SIGN_IN = "needs_sign_in"
ACCOUNT_NEEDS_SCOPES = "needs_scopes"


def account_problem(session: Session, adapter: LoadedAdapter, config: SyncConfig) -> str | None:
    """Why the sync's account can't be used right now, or None."""
    if not adapter.needs_connection:
        return None
    connection = connections.get(session, config.connection_key)
    if connection is None or adapter.preset is None or connection.provider != adapter.provider:
        return ACCOUNT_MISSING
    if adapter.preset.instance and not connection.instance.get("base_url"):
        return ACCOUNT_MISSING
    if connection.status == CONNECTION_NEEDS_SIGN_IN:
        return ACCOUNT_NEEDS_SIGN_IN
    if adapter.preset.missing_scopes(connection.scopes, adapter.scopes):
        return ACCOUNT_NEEDS_SCOPES
    return None


def web_client(session: Session, adapter: LoadedAdapter, connection_key: str, *, read_only: bool = False,
               vouched: list[str] | None = None) -> WebClient:
    """The adapter's web access through one account: its secret applied by kind, and
    a self-hosted service's server address in place of {base_url}."""
    assert adapter.preset is not None and adapter.published_code_hash is not None
    http = make_http()
    connection = connections.get(session, connection_key)
    instance = connection.instance if connection is not None else {}
    return WebClient(provider=bind_instance(adapter.preset, instance), grants=list(adapter.grants),
                     code_hash=adapter.published_code_hash, tokens=None,
                     credentials=credentials.for_connection(session.get_bind(), connection_key, adapter.preset, http),
                     base_url=instance.get("base_url") if adapter.preset.instance else None,
                     read_only=read_only, vouched_hashes=set(vouched or []), http=http)


def hosts(session: Session, adapter: LoadedAdapter, connection_key: str | None) -> list[str]:
    """The hosts the adapter may call through this account, for the review before
    turning a sync on: a self-hosted service's own server, once signed in."""
    if adapter.preset is not None and adapter.preset.instance:
        connection = connections.get(session, connection_key)
        host = instance_host(connection.instance) if connection is not None else None
        return [host] if host else []
    return adapter.hosts


def attach(session: Session, adapter: LoadedAdapter, config: SyncConfig, *, read_only: bool = False,
           vouched: list[str] | None = None, should_cancel: Callable[[], bool] | None = None) -> WebClient | None:
    """Bind the adapter's handler to the sync's account. None for built-ins."""
    if not isinstance(adapter.handler, (StarlarkSourceHandler, StarlarkTargetHandler)) or not config.connection_key:
        return None
    client = web_client(session, adapter, config.connection_key, read_only=read_only, vouched=vouched)
    adapter.handler.attach(client, should_cancel)
    return client
