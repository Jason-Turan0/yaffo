"""Import syncs choose how new files are arranged: `import_root.layout`.

Syncs created before the choice existed arranged imports by year and month taken, so
they get `"layout": "year_month"` and keep doing that (new syncs default to keeping
the source's folders). The layout is part of what a sync's consent covers, so adding
it changes the consent hash. A sync whose stored hash matched its setup is
re-stamped with the new hash, so it stays on; one whose hash didn't match was already
waiting to be turned on again and is left as it is.

The hash below is a frozen copy of yaffo/sync/consent.py and adapters.grants_hash as
they are at this migration, so later changes to those can't change what this does.

Data only: no schema change, nothing to mirror in 000_INIT. Idempotent.
"""
import hashlib
import json
import sqlite3

LAYOUT_YEAR_MONTH = "year_month"


def _grants_hash(grants: list, provider: str, scopes: list) -> str:
    payload = {
        "provider": provider,
        "scopes": sorted(scopes),
        "grants": sorted([g["method"], g.get("url_pattern") or "", g["grant_kind"], g["risk"]] for g in grants),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _adapter_part(conn: sqlite3.Connection, adapter_id: int) -> dict | None:
    row = conn.execute("SELECT handler, provider, grants, published FROM sync_adapters WHERE id = ?",
                       (adapter_id,)).fetchone()
    if row is None:
        return None
    handler, provider, grants, published = row
    if handler:
        return {"id": adapter_id, "code_hash": None, "handler": handler}
    metadata = (json.loads(published) if published else {}).get("metadata") or {}
    code_hash = _grants_hash(json.loads(grants or "[]"), provider, list(metadata.get("scopes") or []))
    return {"id": adapter_id, "code_hash": code_hash, "handler": None}


def _consent_hash(config: dict, adapter: dict) -> str:
    selection = config.get("selection")
    payload = {
        "role": config["role"],
        "adapter": adapter,
        "connection_key": config.get("connection_key"),
        "scopes_needed": sorted(config.get("scopes_needed") or []),
        "options": config.get("options"),
        "import_root": config.get("import_root"),
        "selection": None if selection is None else {
            "kind": selection.get("kind"), "album_id": selection.get("album_id"), "query": selection.get("query"),
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def migrate(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT id, sync FROM automations WHERE sync IS NOT NULL").fetchall()
    for automation_id, raw in rows:
        try:
            column = json.loads(raw)
            config = column["config"]
        except (ValueError, KeyError, TypeError):
            continue
        root = config.get("import_root")
        if config.get("role") != "source" or not isinstance(root, dict) or "layout" in root:
            continue
        adapter = _adapter_part(conn, config.get("adapter_id"))
        was_valid = (adapter is not None and config.get("consent_hash") is not None
                     and config["consent_hash"] == _consent_hash(config, adapter))
        root["layout"] = LAYOUT_YEAR_MONTH
        if was_valid:
            config["consent_hash"] = _consent_hash(config, adapter)
        conn.execute("UPDATE automations SET sync = ? WHERE id = ?", (json.dumps(column), automation_id))
