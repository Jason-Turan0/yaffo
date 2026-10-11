"""The service builder's live-read permission: `sync_adapters.live_reads_key`.

When the user lets the builder make read-only calls to their account while building
(the design's permission 3), this holds the connection key of the test account they
approved it for. The builder's own Test then reads live through that account only; a
different test account, or a revoke, needs the user's approval again. NULL: the
builder's Test records its reads instead.

Mirrored in yaffo/db/models.py and 016 (sync_adapters). Idempotent.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sync_adapters)")}
    if "live_reads_key" not in columns:
        conn.execute("ALTER TABLE sync_adapters ADD COLUMN live_reads_key TEXT")
