"""Albums: `albums` (curated collections) + `album_items` (membership), and the
`share_grants.album_id` column that turns on the album grant scope. Mirrored in
yaffo/db/models.py; see docs/development/p2p-sharing.md (Phase 7).

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            cover_media_item_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cover_media_item_id) REFERENCES media_items(id) ON DELETE SET NULL
        )
    """)
    # Membership. Composite PK (album_id, media_item_id) makes a photo appearing
    # twice in one album impossible at the storage layer, so add-if-missing is an
    # INSERT OR IGNORE rather than a read-then-write.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS album_items (
            album_id INTEGER NOT NULL,
            media_item_id INTEGER NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (album_id, media_item_id),
            FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE,
            FOREIGN KEY (media_item_id) REFERENCES media_items(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_album_items_media_item_id ON album_items(media_item_id)"
    )

    # The album grant scope (share_grants arrived in 006 without this column).
    columns = {row[1] for row in conn.execute("PRAGMA table_info(share_grants)")}
    if "album_id" not in columns:
        conn.execute("ALTER TABLE share_grants ADD COLUMN album_id INTEGER REFERENCES albums(id)")
