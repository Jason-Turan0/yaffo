import sqlite3

import pytest

from yaffo.scripts.db.migrate import _applied_numbers

pytestmark = pytest.mark.unit


def test_applied_numbers_accepts_legacy_and_zero_padded_migration_names():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMP)"
    )
    connection.executemany(
        "INSERT INTO schema_migrations (name) VALUES (?)",
        [
            ("0_MIGRATION_20260620_INIT",),
            ("001_MIGRATION_20260622_rename_photos_to_media_items",),
            ("not-a-migration",),
        ],
    )

    assert _applied_numbers(connection) == {0, 1}
