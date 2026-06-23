"""The SQLite connect-time PRAGMAs: WAL is always on, and `synchronous` reflects the
[database] synchronous config knob (validated to NORMAL/FULL) on every connection."""
import sqlite3

import pytest
from sqlalchemy import create_engine, text

from yaffo.db import _SYNCHRONOUS

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
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000
    finally:
        engine.dispose()
