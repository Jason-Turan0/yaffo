"""Rename the photo entity to media_item (Phase 0 of video support, docs/development/video.md).

A pure rename, no behaviour change: the `photos` table becomes `media_items` and
`photo_labels` becomes `media_labels`; the child foreign-key columns
`faces.photo_id`, `tags.photo_id`, and `media_labels.photo_id` become
`media_item_id`. With `legacy_alter_table` OFF, SQLite's RENAME TO rewrites the FK
references in the child tables from `photos` to `media_items`, and RENAME COLUMN
updates the column's own FK + index references — so the FKs stay valid without a
recreate-and-copy. (This sqlite build defaults the pragma ON, which would leave the
child FKs dangling at `photos`, so we flip it for the renames and restore it.)

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    (legacy_was_on,) = cursor.execute("PRAGMA legacy_alter_table").fetchone()
    cursor.execute("PRAGMA legacy_alter_table=OFF")

    cursor.execute("ALTER TABLE photos RENAME TO media_items")
    cursor.execute("ALTER TABLE photo_labels RENAME TO media_labels")
    cursor.execute("ALTER TABLE faces RENAME COLUMN photo_id TO media_item_id")
    cursor.execute("ALTER TABLE tags RENAME COLUMN photo_id TO media_item_id")
    cursor.execute("ALTER TABLE media_labels RENAME COLUMN photo_id TO media_item_id")

    # Re-point the indexes' names to the new table/column names (the column rename
    # already kept them valid; this only refreshes the identifiers).
    index_renames = [
        ("idx_photos_full_file_path", "idx_media_items_full_file_path", "media_items", "full_file_path"),
        ("idx_photos_date_taken", "idx_media_items_date_taken", "media_items", "date_taken"),
        ("idx_photos_location_name", "idx_media_items_location_name", "media_items", "location_name"),
        ("idx_face_photo_id", "idx_faces_media_item_id", "faces", "media_item_id"),
        ("idx_tags_photo_id", "idx_tags_media_item_id", "tags", "media_item_id"),
        ("idx_photo_labels_photo_id", "idx_media_labels_media_item_id", "media_labels", "media_item_id"),
    ]
    for old_name, new_name, table, column in index_renames:
        cursor.execute(f"DROP INDEX IF EXISTS {old_name}")
        cursor.execute(f"CREATE INDEX IF NOT EXISTS {new_name} ON {table}({column})")

    cursor.execute(f"PRAGMA legacy_alter_table={'ON' if legacy_was_on else 'OFF'}")
