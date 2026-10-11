"""The custom-adapter builder: a chat per custom adapter, like the automation builder's.

- `sync_adapters.builder_status`: the builder run's state for a custom adapter
  (IN_PROGRESS while the model works, READY, FAILED), NULL for built-ins and presets.
  `status` stays the adapter's own validity (ready / invalid).
- `conversations.sync_adapter_id`: a builder conversation belongs to its adapter, as
  an automation builder's belongs to its automation.

Mirrored in yaffo/db/models.py, 016 (sync_adapters) and 000_INIT (conversations).
Idempotent: each column is added only when it's missing.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection) -> None:
    if "builder_status" not in _columns(conn, "sync_adapters"):
        conn.execute("ALTER TABLE sync_adapters ADD COLUMN builder_status TEXT")
    if "sync_adapter_id" not in _columns(conn, "conversations"):
        conn.execute("ALTER TABLE conversations ADD COLUMN sync_adapter_id INTEGER "
                     "REFERENCES sync_adapters(id) ON DELETE CASCADE")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_sync_adapter_id ON conversations(sync_adapter_id)")
