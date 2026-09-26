import importlib
import sqlite3

import pytest

pytestmark = pytest.mark.unit


def _columns(connection, table):
    return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]


def test_migration_creates_the_assistant_tables_and_is_repeatable():
    migration = importlib.import_module(
        "yaffo.scripts.db.migrations.011_MIGRATION_20260925_add_assistant_tables"
    )
    connection = sqlite3.connect(":memory:")

    migration.migrate(connection)
    migration.migrate(connection)  # IF NOT EXISTS: safe after 000_INIT created them

    assert _columns(connection, "assistant_conversations") == [
        "id", "title", "status", "model_id", "run_started_at", "created_at", "updated_at",
    ]
    assert _columns(connection, "assistant_events") == [
        "id", "conversation_id", "seq", "kind", "content", "payload", "created_at",
    ]
    connection.execute("INSERT INTO assistant_conversations (title) VALUES ('q')")
    connection.execute("INSERT INTO assistant_events (conversation_id, seq, kind) VALUES (1, 0, 'user')")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO assistant_events (conversation_id, seq, kind) VALUES (1, 0, 'user')")
    assert connection.execute("SELECT status FROM assistant_conversations").fetchone() == ("IDLE",)
