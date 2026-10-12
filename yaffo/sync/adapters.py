"""Load an adapter row together with the handler that runs it.

Built-in adapters (`sync_adapters.handler` set) map to Python handlers. Starlark
adapters (presets now, custom ones later) are wrapped in handlers that call their
hooks (starlark_adapter.py); their published version must pass the current
contract, or they can't run and the sync pauses with `invalid_config`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session

from yaffo.db.models import (
    SYNC_ADAPTER_STATUS_INVALID, SYNC_HANDLER_FOLDER_SOURCE, SYNC_HANDLER_FOLDER_TARGET, SYNC_HANDLER_PEER_SOURCE,
    SYNC_ROLE_SOURCE, SYNC_ROLE_TARGET, SyncAdapter,
)
from yaffo.sync import folder_source, folder_target, peer_source
from yaffo.sync.adapter_static import CURRENT_CONTRACT
from yaffo.sync.handlers import SourceHandler, TargetHandler
from yaffo.sync import secrets_store
from yaffo.sync.providers import CUSTOM_PREFIX, ConnectionBlockError, ProviderPreset, get_provider, provider_from_block
from yaffo.sync.schemas import AdapterVersion, SyncSchemaError, WebGrant
from yaffo.sync.starlark_adapter import StarlarkSourceHandler, StarlarkTargetHandler


@dataclass(frozen=True)
class BuiltinAdapter:
    role: str
    options: dict[str, dict[str, Any]]
    # Called for every load: a handler that keeps per-run state (the peer source's
    # session and downloads) must not be shared between runs.
    make_handler: Callable[[], SourceHandler | TargetHandler]


_FOLDER_SOURCE = folder_source.FolderSource()
_FOLDER_TARGET = folder_target.FolderTarget()

BUILTINS: dict[str, BuiltinAdapter] = {
    SYNC_HANDLER_FOLDER_SOURCE: BuiltinAdapter(SYNC_ROLE_SOURCE, folder_source.OPTIONS, lambda: _FOLDER_SOURCE),
    SYNC_HANDLER_FOLDER_TARGET: BuiltinAdapter(SYNC_ROLE_TARGET, folder_target.OPTIONS, lambda: _FOLDER_TARGET),
    SYNC_HANDLER_PEER_SOURCE: BuiltinAdapter(SYNC_ROLE_SOURCE, peer_source.OPTIONS, peer_source.PeerSource),
}


class AdapterUnavailable(Exception):
    """The adapter row is missing, invalid, or has nothing this build can run."""


@dataclass(frozen=True)
class LoadedAdapter:
    id: int
    slug: str
    name: str
    role: str
    provider: str
    handler_key: str | None
    # What the consent covers of the adapter itself: None for a built-in (its
    # handler is covered instead), the grant-set hash for a Starlark adapter.
    code_hash: str | None
    options: dict[str, dict[str, Any]]
    handler: SourceHandler | TargetHandler
    metadata: dict[str, Any] = field(default_factory=dict)
    grants: tuple[WebGrant, ...] = ()
    published_code_hash: str | None = None
    preset: ProviderPreset | None = None

    @property
    def is_builtin(self) -> bool:
        return self.handler_key is not None

    @property
    def needs_connection(self) -> bool:
        return not self.is_builtin

    @property
    def scopes(self) -> list[str]:
        return list(self.metadata.get("scopes") or [])

    @property
    def hosts(self) -> list[str]:
        """The hosts the adapter may call, for the review before turning a sync on."""
        if self.preset is None:
            return []
        return [base.split("://", 1)[-1] for base in self.preset.base_urls]

    @property
    def has_choices(self) -> bool:
        if self.is_builtin:
            return callable(getattr(self.handler, "choices", None))
        return "choices" in (self.metadata.get("hooks") or {})


def grants_hash(grants: list[WebGrant], provider: str, scopes: list[str], connection: dict | None = None) -> str:
    """The part of an adapter a sync's consent covers: what it may call, with which
    account kind and scopes, and a custom adapter's connection block (its hosts and
    sign-in). Code changes that keep these leave the consent valid (the grants
    themselves are pinned to the code hash)."""
    payload = {
        "provider": provider,
        "scopes": sorted(scopes),
        "grants": sorted([g.method, g.url_pattern or "", g.grant_kind, g.risk] for g in grants),
    }
    if connection is not None:
        payload["connection"] = connection
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def custom_provider(row: SyncAdapter, metadata: dict[str, Any]) -> ProviderPreset:
    """A custom adapter's provider, from its connection block. Its OAuth app (client id
    and secret) is the user's own, in the keychain under the adapter."""
    ref = row.app_secret_ref
    try:
        return provider_from_block(CUSTOM_PREFIX + row.slug, row.name, metadata.get("connection"),
                                   app_credentials=lambda: (secrets_store.load_secret(ref) or {}) if ref else {})
    except ConnectionBlockError as e:
        raise AdapterUnavailable(f"adapter {row.slug!r} has an invalid connection block: {e}") from e


def provider_for_key(session: Session, key: str) -> ProviderPreset | None:
    """The provider a connection names: a preset, or a custom adapter's (from its
    draft while it's being built, so its test account can sign in)."""
    if not key.startswith(CUSTOM_PREFIX):
        return get_provider(key)
    row = session.query(SyncAdapter).filter(SyncAdapter.slug == key[len(CUSTOM_PREFIX):]).first()
    version = (row.working or row.published) if row is not None else None
    if not version:
        return None
    try:
        return custom_provider(row, version.get("metadata") or {})
    except AdapterUnavailable:
        return None


def _starlark(row: SyncAdapter, working: bool = False) -> LoadedAdapter:
    """A Starlark adapter's published version, or, for the builder's Test, its draft
    with every endpoint the static check found granted (Test never sends a write)."""
    stored, code = (row.working, row.working_code) if working else (row.published, row.published_code)
    if not working and row.status == SYNC_ADAPTER_STATUS_INVALID or not stored or not code:
        raise AdapterUnavailable(f"adapter {row.slug!r} isn't published or failed its check")
    try:
        version = AdapterVersion.from_dict(stored)
        grants = [WebGrant.from_dict(g) for g in (_draft_grants(version) if working else row.grants or [])]
    except SyncSchemaError as e:
        raise AdapterUnavailable(f"adapter {row.slug!r} has invalid stored JSON: {e}") from e
    if version.contract != CURRENT_CONTRACT:
        raise AdapterUnavailable(f"adapter {row.slug!r} is on contract {version.contract}, not {CURRENT_CONTRACT}")
    if version.metadata.get("role") != row.role and not working:
        raise AdapterUnavailable(f"adapter {row.slug!r} has a mismatched role")
    role = version.metadata.get("role")
    connection = None
    if version.metadata.get("provider") == "custom":
        connection = version.metadata.get("connection")
        preset = custom_provider(row, version.metadata)
    else:
        preset = get_provider(row.provider)
    if preset is None:
        raise AdapterUnavailable(f"adapter {row.slug!r} needs provider {row.provider!r}, which this build lacks")
    if role == SYNC_ROLE_SOURCE:
        handler = StarlarkSourceHandler(code, version.metadata, preset.label)
    elif role == SYNC_ROLE_TARGET:
        handler = StarlarkTargetHandler(code, version.metadata, preset.label)
    else:
        raise AdapterUnavailable(f"Starlark {row.role} adapters can't run in this version")
    scopes = list(version.metadata.get("scopes") or [])
    return LoadedAdapter(id=row.id, slug=row.slug, name=row.name, role=role, provider=preset.key,
                         handler_key=None, code_hash=grants_hash(grants, preset.key, scopes, connection),
                         options=dict(version.metadata.get("options") or {}), handler=handler,
                         metadata=version.metadata, grants=tuple(grants), published_code_hash=version.code_hash,
                         preset=preset)


def _draft_grants(version: AdapterVersion) -> list[dict]:
    return [{"method": entry["method"], "url_pattern": entry["url_pattern"], "grant_kind": entry["grant_kind"],
             "followup_of": None, "risk": entry["risk"], "granted_code_hash": version.code_hash,
             "granted_at": "draft"} for entry in version.manifest]


def load_working(session: Session, adapter_id: int) -> LoadedAdapter:
    """A custom adapter's draft, for the builder's Test."""
    row = session.get(SyncAdapter, adapter_id)
    if row is None or row.handler or row.is_preset:
        raise AdapterUnavailable(f"adapter {adapter_id} isn't a custom adapter")
    return _starlark(row, working=True)


def _from_row(row: SyncAdapter) -> LoadedAdapter:
    if not row.handler:
        return _starlark(row)
    builtin = BUILTINS.get(row.handler or "")
    if builtin is None or builtin.role != row.role:
        raise AdapterUnavailable(f"adapter {row.slug!r} can't run in this version")
    return LoadedAdapter(id=row.id, slug=row.slug, name=row.name, role=row.role, provider=row.provider,
                         handler_key=row.handler, code_hash=None, options=builtin.options,
                         handler=builtin.make_handler())


def load_adapter(session: Session, adapter_id: int) -> LoadedAdapter:
    row = session.get(SyncAdapter, adapter_id)
    if row is None:
        raise AdapterUnavailable(f"adapter {adapter_id} doesn't exist")
    if row.is_preset and not row.handler:
        from yaffo.sync.preset_seed import ensure_presets

        ensure_presets(session)
        session.refresh(row)
    return _from_row(row)


def runnable_adapters(session: Session, role: str) -> list[LoadedAdapter]:
    """Adapters a new sync of this role can use: built-ins first, then presets,
    then by name."""
    from yaffo.sync.preset_seed import ensure_presets

    ensure_presets(session)
    rows = (session.query(SyncAdapter)
            .filter(SyncAdapter.role == role)
            .order_by(SyncAdapter.is_preset.desc(), SyncAdapter.handler.is_(None), SyncAdapter.name)
            .all())
    loaded = []
    for row in rows:
        try:
            loaded.append(_from_row(row))
        except AdapterUnavailable:
            continue
    return loaded


def target_handler(adapter: LoadedAdapter) -> TargetHandler:
    assert isinstance(adapter.handler, TargetHandler)
    return adapter.handler


def source_handler(adapter: LoadedAdapter) -> SourceHandler:
    assert isinstance(adapter.handler, SourceHandler)
    return adapter.handler
