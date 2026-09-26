"""Assistant change plans: the mutating calls a script recorded in preview, frozen
for the user to approve, replay and undo. Mirrored in yaffo/db/models.py and
000_INIT; see docs/development/ai-assistant.md → Change plans.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assistant_change_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            script TEXT NOT NULL DEFAULT '',
            steps_json TEXT NOT NULL,
            risk TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            decided_at TIMESTAMP,
            finished_at TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES assistant_conversations(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assistant_change_plans_conversation "
        "ON assistant_change_plans(conversation_id)"
    )
