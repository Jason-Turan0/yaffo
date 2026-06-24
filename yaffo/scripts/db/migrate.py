"""Database migration runner.

Migrations are numbered Python modules under `migrations/`, each exposing
`migrate(conn)` and written in the raw-sqlite3 style of the INIT migration. They
run in numeric order; the ones already recorded in the `schema_migrations` table
are skipped, so every migration applies exactly once. Each runs in its own
transaction (committed only if it succeeds). Before applying anything, the current
database is copied to a timestamped backup.

    from yaffo.scripts.db.migrate import run_migrations
    run_migrations()

To add a migration: drop `N_MIGRATION_<yyyymmdd>_<name>.py` into `migrations/`
with the next number and a `def migrate(conn): ...`. Do not commit inside it.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from yaffo.common import BUNDLE_ROOT, DB_PATH, ROOT_DIR
from yaffo.logging_config import get_logger

logger = get_logger(__name__)

# Frozen by PyInstaller the migrations are shipped as data under sys._MEIPASS (the
# spec adds them there); in dev they sit beside this module. They're loaded by file
# path, not imported as modules — so they ship as data and the digit-prefixed
# filenames (illegal module names) don't matter.
if getattr(sys, "frozen", False):
    MIGRATIONS_DIR = BUNDLE_ROOT / "yaffo" / "scripts" / "db" / "migrations"
else:
    MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_NAME_RE = re.compile(r"^(\d+)_.*\.py$")


def _discover() -> list[tuple[int, Path]]:
    """All migrations as (number, path), ordered by their numeric prefix."""
    found = []
    for path in MIGRATIONS_DIR.glob("*.py"):
        match = _NAME_RE.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    found.sort(key=lambda item: item[0])
    return found


def _load_migration(path: Path):
    spec = importlib.util.spec_from_file_location(f"_yaffo_migration_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _applied_numbers(conn: sqlite3.Connection) -> set[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  name TEXT PRIMARY KEY,"
        "  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.commit()
    applied = set()
    for name, in conn.execute("SELECT name FROM schema_migrations"):
        match = _NAME_RE.match(f"{name}.py")
        if match:
            applied.add(int(match.group(1)))
    return applied


def _backup() -> Path:
    backups = ROOT_DIR / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    dest = backups / f"{DB_PATH.stem}-{datetime.now():%Y%m%d_%H%M%S}{DB_PATH.suffix}"
    shutil.copy2(DB_PATH, dest)
    logger.info(f"backed up database to {dest}")
    return dest


def run_migrations() -> None:
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    db_existed = DB_PATH.exists()  # connecting below would create it

    # autocommit=False (PEP 249 semantics) so each migration — DDL included — runs
    # inside one transaction we commit on success or roll back on failure.
    conn = sqlite3.connect(DB_PATH, autocommit=False)
    try:
        applied_numbers = _applied_numbers(conn)
        pending = [(number, path) for number, path in _discover() if number not in applied_numbers]
        if not pending:
            logger.info("database schema up to date")
            return

        # snapshot before mutating an existing database; surfaced on failure so a
        # manual restore is a single copy. The failed migration is rolled back, so
        # the live DB stays consistent — the backup is a belt-and-suspenders.
        backup = _backup() if db_existed else None
        for number, path in pending:
            try:
                _load_migration(path).migrate(conn)
                conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (path.stem,))
                conn.commit()
                logger.info(f"applied migration {path.stem}")
            except Exception:
                conn.rollback()
                logger.exception(f"migration {path.stem} failed; rolled back")
                if backup is not None:
                    logger.error(f"to restore the pre-migration database: cp '{backup}' '{DB_PATH}'")
                raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_migrations()
