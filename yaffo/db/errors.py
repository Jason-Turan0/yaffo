import sqlite3

from sqlalchemy.exc import OperationalError


def is_lock_error(exc: Exception) -> bool:
    """Whether SQLite rejected an operation because another writer holds its lock."""
    return isinstance(exc, (OperationalError, sqlite3.OperationalError)) and "database is locked" in str(exc)
