import sqlite3

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

from yaffo.config import get as get_config

db = SQLAlchemy()

# PRAGMA synchronous from config ([database] synchronous), default NORMAL. Only
# NORMAL/FULL are honoured (a typo falls back to NORMAL) so the pragma can't be fed
# arbitrary text. Read once at import; edit config.toml + restart to change.
_SYNCHRONOUS = str(get_config("database", "synchronous", "NORMAL")).upper()
if _SYNCHRONOUS not in ("NORMAL", "FULL"):
    _SYNCHRONOUS = "NORMAL"


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """SQLite is single-writer. The app opens the one app.db from two pools — the
    Flask request engine and the background worker's engine — so writes race. Without
    these PRAGMAs a concurrent write fails immediately with "database is locked"
    (e.g. saving an automation's config while its classify job is committing labels).

    - WAL lets readers run alongside the single writer (and survives across the file).
    - busy_timeout makes a writer wait for the lock instead of erroring at once.
    - synchronous (config [database] synchronous, default NORMAL): NORMAL is the
      standard, fast pairing with WAL (only the last txn is at risk on an OS/disk
      failure, never corruption) and avoids an fsync per commit; FULL fsyncs every
      commit (no lost writes on an OS/disk failure) at a write-throughput cost.

    Registered on Engine so it covers every engine in the process, including tests.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute(f"PRAGMA synchronous={_SYNCHRONOUS}")
    cursor.close()
