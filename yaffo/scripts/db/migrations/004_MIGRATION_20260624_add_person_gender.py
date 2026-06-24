"""Add nullable user-specified gender to people.

The home gallery prefers this value over the per-face InsightFace estimate when
it is present. Existing people remain NULL and therefore retain estimated-gender
filtering behavior.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE people ADD COLUMN gender INTEGER")
