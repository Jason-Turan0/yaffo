"""Rename the photo-entity domain events to media events (consistency pass for the
media_item rename, docs/development/video.md).

The events describe the media entity and fire for videos too, so `photo_*` →
`media_*`: `photo_imported`→`media_imported`, `photo_indexed`→`media_indexed`,
`photo_modified`→`media_modified`, `photo_labeled`→`media_labeled`. Only the seeded
`automation_triggers.event_type` values reference these strings in the DB; this
re-points the built-in automations' event triggers to the new names. (The INIT
migration stays frozen, seeding the old names; this converts them.)

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3

_EVENT_RENAMES = [
    ("photo_imported", "media_imported"),
    ("photo_indexed", "media_indexed"),
    ("photo_modified", "media_modified"),
    ("photo_labeled", "media_labeled"),
]


def migrate(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    for old, new in _EVENT_RENAMES:
        cursor.execute(
            "UPDATE automation_triggers SET event_type = ? WHERE event_type = ?",
            (new, old),
        )
