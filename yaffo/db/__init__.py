import logging
import sqlite3
import time

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

from yaffo.config import get as get_config, get_int as get_config_int

db = SQLAlchemy()

# PRAGMA synchronous from config ([database] synchronous), default NORMAL. Only
# NORMAL/FULL are honoured (a typo falls back to NORMAL) so the pragma can't be fed
# arbitrary text. Read once at import; edit config.toml + restart to change.
_SYNCHRONOUS = str(get_config("database", "synchronous", "NORMAL")).upper()
if _SYNCHRONOUS not in ("NORMAL", "FULL"):
    _SYNCHRONOUS = "NORMAL"

# How long a writer waits for SQLite's single write lock before "database is locked"
# ([database] busy_timeout_ms). Background jobs share the lock; a wait of a few
# seconds is normal while several run, and failing a long run over it is not.
_BUSY_TIMEOUT_MS = get_config_int("database", "busy_timeout_ms", 30000)

# Statements slower than this many ms are logged ([database] slow_query_ms); 0 is off.
_SLOW_QUERY_MS = get_config_int("database", "slow_query_ms", 0, minimum=0)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """SQLite is single-writer. The app opens the one app.db from two pools — the
    Flask request engine and the background worker's engine — so writes race. Without
    these PRAGMAs a concurrent write fails immediately with "database is locked"
    (e.g. saving an automation's config while its classify job is committing labels).

    - WAL lets readers run alongside the single writer (and survives across the file).
    - busy_timeout (config [database] busy_timeout_ms, default 30 s) makes a writer
      wait for the lock instead of erroring at once.
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
    cursor.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    cursor.execute(f"PRAGMA synchronous={_SYNCHRONOUS}")
    cursor.close()


def log_slow_queries(engine: Engine, logger: logging.Logger) -> None:
    """Log every statement on `engine` slower than [database] slow_query_ms to
    `logger`, with its duration and parameters. Does nothing when the setting is 0
    (the default). Each engine passes its own logger, so web queries land in
    yaffo.log and worker queries in background_tasks.log."""
    if _SLOW_QUERY_MS <= 0:
        return
    threshold = _SLOW_QUERY_MS / 1000

    @event.listens_for(engine, "before_cursor_execute")
    def _start(conn, cursor, statement, parameters, context, executemany):
        conn.info["query_started_at"] = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _finish(conn, cursor, statement, parameters, context, executemany):
        elapsed = time.perf_counter() - conn.info.pop("query_started_at", time.perf_counter())
        if elapsed >= threshold:
            # Parameters are cut short: embeddings are kilobytes of blob.
            logger.warning(f"slow query ({elapsed * 1000:.0f} ms): {' '.join(statement.split())} "
                           f"params={str(parameters)[:300]}")
