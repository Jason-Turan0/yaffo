"""Load an adapter row together with the handler that runs it.

Built-in adapters (`sync_adapters.handler` set) map to Python handlers here. Starlark
adapters (presets and custom) arrive in Phase 2; until then a row without a known
handler can't run, and the sync pauses with `invalid_config`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from yaffo.db.models import (
    SYNC_HANDLER_FOLDER_SOURCE, SYNC_HANDLER_FOLDER_TARGET, SYNC_ROLE_SOURCE, SYNC_ROLE_TARGET, SyncAdapter,
)
from yaffo.sync import folder_source, folder_target
from yaffo.sync.handlers import SourceHandler, TargetHandler


@dataclass(frozen=True)
class BuiltinAdapter:
    role: str
    options: dict[str, dict[str, Any]]
    handler: SourceHandler | TargetHandler


BUILTINS: dict[str, BuiltinAdapter] = {
    SYNC_HANDLER_FOLDER_SOURCE: BuiltinAdapter(SYNC_ROLE_SOURCE, folder_source.OPTIONS, folder_source.FolderSource()),
    SYNC_HANDLER_FOLDER_TARGET: BuiltinAdapter(SYNC_ROLE_TARGET, folder_target.OPTIONS, folder_target.FolderTarget()),
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
    code_hash: str | None
    options: dict[str, dict[str, Any]]
    handler: SourceHandler | TargetHandler

    @property
    def is_builtin(self) -> bool:
        return self.handler_key is not None

    @property
    def needs_connection(self) -> bool:
        return not self.is_builtin


def _from_row(row: SyncAdapter) -> LoadedAdapter:
    builtin = BUILTINS.get(row.handler or "")
    if builtin is None or builtin.role != row.role:
        raise AdapterUnavailable(f"adapter {row.slug!r} can't run in this version")
    return LoadedAdapter(id=row.id, slug=row.slug, name=row.name, role=row.role, provider=row.provider,
                         handler_key=row.handler, code_hash=None, options=builtin.options,
                         handler=builtin.handler)


def load_adapter(session: Session, adapter_id: int) -> LoadedAdapter:
    row = session.get(SyncAdapter, adapter_id)
    if row is None:
        raise AdapterUnavailable(f"adapter {adapter_id} doesn't exist")
    return _from_row(row)


def runnable_adapters(session: Session, role: str) -> list[LoadedAdapter]:
    """Adapters a new sync of this role can use, built-ins first then by name."""
    rows = (session.query(SyncAdapter)
            .filter(SyncAdapter.role == role)
            .order_by(SyncAdapter.is_preset.desc(), SyncAdapter.name)
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
