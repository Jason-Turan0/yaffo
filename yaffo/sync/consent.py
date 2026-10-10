"""The consent hash: everything the user approved when turning a sync on.

A hash over the role, the adapter (its published code hash, or its handler for a
built-in), the connection and the scopes the sync needs, the options, the import root,
and the selection (kind plus album id or query). The engine recomputes it at run
start; a mismatch pauses the sync with `needs_consent` before anything is read or
written. Changing any of these (a different album or folder, a new filter, the
removal setting) therefore needs the user's OK again, by construction.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from yaffo.sync.adapters import LoadedAdapter
from yaffo.sync.schemas import SyncConfig
from yaffo.sync.schemas import SELECTION_SCRIPT


def consent_payload(config: SyncConfig, adapter: LoadedAdapter, selector_code: str | None = None) -> dict[str, Any]:
    selection = config.selection
    return {
        "role": config.role,
        "adapter": {"id": adapter.id, "code_hash": adapter.code_hash, "handler": adapter.handler_key},
        "connection_key": config.connection_key,
        "scopes_needed": sorted(config.scopes_needed),
        "options": config.options,
        "import_root": config.import_root,
        "selection": None if selection is None else {
            "kind": selection.kind, "album_id": selection.album_id, "query": selection.query,
            **({"script_hash": hashlib.sha256((selector_code or "").encode("utf-8")).hexdigest()}
               if selection.kind == SELECTION_SCRIPT else {}),
        },
    }


def consent_hash(config: SyncConfig, adapter: LoadedAdapter, selector_code: str | None = None) -> str:
    canonical = json.dumps(consent_payload(config, adapter, selector_code), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def has_consent(config: SyncConfig, adapter: LoadedAdapter, selector_code: str | None = None) -> bool:
    return config.consent_hash is not None and config.consent_hash == consent_hash(config, adapter, selector_code)
