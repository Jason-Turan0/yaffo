import sqlite3

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """SQLite is single-writer. The app opens the one app.db from two pools — the
    Flask request engine and the background worker's engine — so writes race. Without
    these PRAGMAs a concurrent write fails immediately with "database is locked"
    (e.g. saving an automation's config while its classify job is committing labels).

    - WAL lets readers run alongside the single writer (and survives across the file).
    - busy_timeout makes a writer wait for the lock instead of erroring at once.
    - synchronous=NORMAL is the standard, safe pairing with WAL (only the last txn is
      at risk on an OS crash, never corruption) and avoids an fsync per commit.

    Registered on Engine so it covers every engine in the process, including tests.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()
