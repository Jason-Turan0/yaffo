"""Record why a media item couldn't be indexed.

`media_items.status` gains the value FAILED: indexing failed in a way retrying
won't fix (a damaged or unsupported file). These columns say why (`index_error`,
a code the UI translates; `index_error_detail`, the English error), when
(`index_failed_at`), and what the file was then (`index_failed_signature`,
size:mtime). File sync skips FAILED items until that signature changes or the
user asks to retry. See utils/index_errors.py. Mirrored in yaffo/db/models.py and
000_INIT.

Existing rows need nothing: items that failed before this are still IMPORTED and
get the new treatment on their next attempt.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3

_COLUMNS = {
    "index_error": "TEXT",
    "index_error_detail": "TEXT",
    "index_failed_at": "TIMESTAMP",
    "index_failed_signature": "TEXT",
}


def migrate(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(media_items)")}
    for name, sql_type in _COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE media_items ADD COLUMN {name} {sql_type}")
