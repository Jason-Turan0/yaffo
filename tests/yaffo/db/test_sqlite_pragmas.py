"""The SQLite connect-time PRAGMAs: WAL is always on, and `synchronous` reflects the
[database] synchronous config knob (validated to NORMAL/FULL) on every connection."""
import logging
import sqlite3

import pytest
from sqlalchemy import create_engine, text

import yaffo.db as yaffo_db
from yaffo.db import _BUSY_TIMEOUT_MS, _SYNCHRONOUS

pytestmark = pytest.mark.unit

# PRAGMA synchronous returns an int code, not the name.
_CODE = {"NORMAL": 1, "FULL": 2}


def test_synchronous_is_validated_to_a_known_mode():
    assert _SYNCHRONOUS in ("NORMAL", "FULL")


def test_connect_listener_applies_wal_and_configured_synchronous(tmp_path):
    # A real on-disk SQLite DB so journal_mode=WAL can actually engage (it's a no-op
    # for :memory:). The connect listener is registered on the global Engine class.
    engine = create_engine(f"sqlite:///{tmp_path/'probe.db'}")
    try:
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
            assert conn.execute(text("PRAGMA synchronous")).scalar() == _CODE[_SYNCHRONOUS]
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() == _BUSY_TIMEOUT_MS
    finally:
        engine.dispose()


def test_slow_queries_are_logged_with_duration_and_params(tmp_path, monkeypatch):
    monkeypatch.setattr(yaffo_db, "_SLOW_QUERY_MS", 1)
    engine = create_engine(f"sqlite:///{tmp_path/'slow.db'}")
    logger = logging.getLogger("test.slow_queries")
    records = []
    monkeypatch.setattr(logger, "warning", lambda message: records.append(message))
    yaffo_db.log_slow_queries(engine, logger)
    try:
        with engine.connect() as conn:
            # A recursive count to 300k: comfortably over 1 ms.
            conn.execute(text(
                "WITH RECURSIVE n(i) AS (SELECT :start UNION ALL SELECT i + 1 FROM n WHERE i < 300000) "
                "SELECT count(*) FROM n"), {"start": 1})
    finally:
        engine.dispose()

    assert any("slow query (" in r and "WITH RECURSIVE" in r and "params=" in r for r in records)


def test_slow_query_logging_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(yaffo_db, "_SLOW_QUERY_MS", 0)
    engine = create_engine(f"sqlite:///{tmp_path/'off.db'}")
    yaffo_db.log_slow_queries(engine, logging.getLogger("test.slow_queries_off"))
    try:
        assert not engine.dispatch.after_cursor_execute
    finally:
        engine.dispose()
