"""`jobs.estimated_completed_at`: when a running job is projected to finish.

Recomputed on every progress tick from the rate since `started_at`
(db/repositories/job_repository.py); NULL until a job has made progress, and on
jobs from before this column existed. Mirrored in yaffo/db/models.py and 000_INIT.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "estimated_completed_at" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN estimated_completed_at TIMESTAMP")
