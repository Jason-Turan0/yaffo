"""Index the sync ledger by (automation_id, updated_at): the Items panel lists a
sync's rows newest first, 25 at a time, and a OneDrive backfill can leave tens of
thousands of them.

Mirrored in yaffo/db/models.py and 000_INIT. Idempotent.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3

SYNC_ITEMS_UPDATED_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_sync_items_updated ON sync_items(automation_id, updated_at)"
)


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(SYNC_ITEMS_UPDATED_INDEX)
