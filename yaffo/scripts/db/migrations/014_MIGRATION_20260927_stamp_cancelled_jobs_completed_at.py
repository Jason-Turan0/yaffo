"""Give every existing CANCELLED job a `completed_at`.

`completed_at` now records when a job's work ended, however it ended, and a
CANCELLED job without one reads "Stopping" (its task hasn't reached its next
cancellation check yet). Before this, cancelled jobs never got a completed_at, so
without this backfill every old cancelled run would read Stopping forever. Their
last update is the closest record of when they stopped.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE jobs SET completed_at = COALESCE(updated_at, created_at) "
        "WHERE status = 'CANCELLED' AND completed_at IS NULL"
    )
