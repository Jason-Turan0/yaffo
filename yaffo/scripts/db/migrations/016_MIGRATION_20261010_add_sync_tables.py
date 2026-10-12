"""Sync automations (docs/development/automation-sync-adapters.md, Phase 1).

- `automations.sync`: JSON, the sync's config and engine runtime; non-NULL routes
  the automation to the sync engine.
- `sync_adapters`: one row per adapter. Seeded with the two built-ins, the Folder
  source and Folder target, which run Python handlers instead of Starlark code.
- `sync_items`: the per-item ledger (what a sync imported or sent), unique on
  (automation_id, item_key).
- `media_items.sha1`: content hash for import dedupe, filled at index time and by
  the `sha1` backfill for rows indexed before this.

Mirrored in yaffo/db/models.py and 000_INIT. Every step is idempotent.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3

SYNC_ADAPTERS_DDL = """
    CREATE TABLE IF NOT EXISTS sync_adapters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        provider TEXT NOT NULL,
        is_preset INTEGER NOT NULL DEFAULT 0,
        preset_version TEXT,
        handler TEXT,
        working_code TEXT,
        published_code TEXT,
        working TEXT,
        published TEXT,
        grants TEXT NOT NULL DEFAULT '[]',
        doc_domains TEXT NOT NULL DEFAULT '[]',
        app_secret_ref TEXT,
        test_connection_key TEXT,
        status TEXT NOT NULL DEFAULT 'ready',
        builder_status TEXT,
        live_reads_key TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

SYNC_ITEMS_DDL = """
    CREATE TABLE IF NOT EXISTS sync_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        automation_id INTEGER NOT NULL,
        item_key TEXT NOT NULL,
        media_item_id INTEGER,
        remote_id TEXT,
        target TEXT,
        fingerprint TEXT,
        status TEXT NOT NULL,
        error_code TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        run_id TEXT,
        details TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_sync_items_automation_key UNIQUE (automation_id, item_key),
        FOREIGN KEY (automation_id) REFERENCES automations(id) ON DELETE CASCADE,
        FOREIGN KEY (media_item_id) REFERENCES media_items(id) ON DELETE SET NULL,
        FOREIGN KEY (run_id) REFERENCES jobs(id) ON DELETE SET NULL
    )
"""

SYNC_ITEMS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_sync_items_status ON sync_items(automation_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_sync_items_media_item ON sync_items(media_item_id)",
)

SEED_BUILTIN_ADAPTERS = """
    INSERT OR IGNORE INTO sync_adapters (slug, name, role, provider, is_preset, preset_version, handler, status)
    VALUES ('builtin.folder_source', 'Folder', 'source', 'local', 1, '1', 'folder_source', 'ready'),
           ('builtin.folder_target', 'Folder', 'target', 'local', 1, '1', 'folder_target', 'ready')
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection) -> None:
    if "sync" not in _columns(conn, "automations"):
        conn.execute("ALTER TABLE automations ADD COLUMN sync TEXT")
    if "sha1" not in _columns(conn, "media_items"):
        conn.execute("ALTER TABLE media_items ADD COLUMN sha1 VARCHAR(40)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_media_items_sha1 ON media_items(sha1)")
    conn.execute(SYNC_ADAPTERS_DDL)
    conn.execute(SYNC_ITEMS_DDL)
    for statement in SYNC_ITEMS_INDEXES:
        conn.execute(statement)
    conn.execute(SEED_BUILTIN_ADAPTERS)
