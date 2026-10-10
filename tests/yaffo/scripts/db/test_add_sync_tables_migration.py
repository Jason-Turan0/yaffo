import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from yaffo.db.models import Automation, MediaItem, SyncAdapter, SyncItem
from yaffo.scripts.db.migrate import MIGRATIONS_DIR, _discover

pytestmark = pytest.mark.unit


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply(connection: sqlite3.Connection, up_to: int) -> None:
    for number, path in _discover():
        if number <= up_to:
            _load(path).migrate(connection)


def _columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _model_columns(model) -> set[str]:
    return {column.name for column in model.__table__.columns}


@pytest.fixture
def fresh_db():
    connection = sqlite3.connect(":memory:")
    _apply(connection, up_to=10_000)
    return connection


def test_fresh_database_gets_the_sync_schema_matching_the_models(fresh_db):
    assert _model_columns(SyncAdapter) == _columns(fresh_db, "sync_adapters")
    assert _model_columns(SyncItem) == _columns(fresh_db, "sync_items")
    assert "sync" in _columns(fresh_db, "automations")
    assert "sha1" in _columns(fresh_db, "media_items")
    assert _model_columns(Automation) <= _columns(fresh_db, "automations")
    assert _model_columns(MediaItem) <= _columns(fresh_db, "media_items")
    indexes = {row[1] for row in fresh_db.execute("PRAGMA index_list(media_items)")}
    assert "ix_media_items_sha1" in indexes


def test_builtin_folder_adapters_are_seeded_once(fresh_db):
    migration = _load(MIGRATIONS_DIR / "016_MIGRATION_20261010_add_sync_tables.py")
    migration.migrate(fresh_db)
    rows = fresh_db.execute(
        "SELECT slug, role, provider, handler, is_preset, grants FROM sync_adapters ORDER BY slug"
    ).fetchall()
    assert rows == [
        ("builtin.folder_source", "source", "local", "folder_source", 1, "[]"),
        ("builtin.folder_target", "target", "local", "folder_target", 1, "[]"),
    ]


def test_existing_database_is_upgraded_and_rerun_is_harmless():
    # A database created before 016 (000_INIT now creates these columns itself).
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE automations (id INTEGER PRIMARY KEY, slug TEXT, name TEXT, config TEXT)")
    connection.execute("CREATE TABLE media_items (id INTEGER PRIMARY KEY, full_file_path TEXT)")
    connection.execute("INSERT INTO media_items (full_file_path) VALUES ('/p/a.jpg')")

    migration = _load(MIGRATIONS_DIR / "016_MIGRATION_20261010_add_sync_tables.py")
    migration.migrate(connection)
    migration.migrate(connection)

    assert {"sync"} <= _columns(connection, "automations")
    assert connection.execute("SELECT sha1 FROM media_items").fetchall() == [(None,)]
    assert connection.execute("SELECT COUNT(*) FROM sync_adapters").fetchone() == (2,)


def test_sync_items_are_unique_per_automation_and_key(fresh_db):
    fresh_db.execute("INSERT INTO automations (slug, name, sync) VALUES ('s', 'S', ?)",
                     (json.dumps({"config": {}, "runtime": {}}),))
    insert = "INSERT INTO sync_items (automation_id, item_key, status) VALUES (1, 'k', 'pending')"
    fresh_db.execute(insert)
    with pytest.raises(sqlite3.IntegrityError):
        fresh_db.execute(insert)
