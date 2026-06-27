"""Add video columns to media_items (Phase 1 of video support, docs/development/video.md).

Additive only: a `media_type` discriminator (backfilled to "photo" for every
existing row) plus the nullable video-only columns. No data is rewritten beyond
the backfill, and photo rows leave the video columns NULL.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    cursor.execute(
        "ALTER TABLE media_items ADD COLUMN media_type TEXT NOT NULL DEFAULT 'photo'"
    )
    cursor.execute("ALTER TABLE media_items ADD COLUMN poster_path TEXT")
    cursor.execute("ALTER TABLE media_items ADD COLUMN duration_seconds REAL")
    cursor.execute("ALTER TABLE media_items ADD COLUMN width INTEGER")
    cursor.execute("ALTER TABLE media_items ADD COLUMN height INTEGER")
    cursor.execute("ALTER TABLE media_items ADD COLUMN video_codec TEXT")

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_items_media_type ON media_items(media_type)"
    )
