"""The in-app assistant: `assistant_conversations` and their transcript,
`assistant_events`. Mirrored in yaffo/db/models.py and 000_INIT; see
docs/development/ai-assistant.md.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assistant_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'IDLE',
            model_id TEXT,
            run_started_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assistant_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            seq INTEGER NOT NULL,
            kind TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            payload TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES assistant_conversations(id) ON DELETE CASCADE,
            CONSTRAINT uq_assistant_events_conversation_seq UNIQUE (conversation_id, seq)
        )
    """)
