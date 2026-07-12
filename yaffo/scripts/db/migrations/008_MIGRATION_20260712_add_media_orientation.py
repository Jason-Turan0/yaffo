"""`media_items.orientation`: the file's EXIF orientation tag (1-8), for search.

NULL on rows indexed before this column existed. Note that face boxes are NOT
rotated by this value — from this migration on, indexing transposes the image
before face detection, so boxes and thumbnails are stored in upright, as-displayed
pixel space. Photos indexed earlier still hold raw-buffer boxes; run
`python -m scripts.backfill_orientation` to fill this column in and rotate those
older boxes (and their thumbnails) into upright space. Mirrored in yaffo/db/models.py.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(media_items)")}
    if "orientation" not in columns:
        conn.execute("ALTER TABLE media_items ADD COLUMN orientation INTEGER")
