import importlib
import sqlite3

import pytest

pytestmark = pytest.mark.unit


def test_migration_adds_nullable_person_gender_column():
    migration = importlib.import_module(
        "yaffo.scripts.db.migrations.004_MIGRATION_20260624_add_person_gender"
    )
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("INSERT INTO people (name) VALUES ('Existing person')")

    migration.migrate(connection)

    columns = {
        row[1]: row
        for row in connection.execute("PRAGMA table_info(people)").fetchall()
    }
    assert columns["gender"][3] == 0
    assert connection.execute("SELECT gender FROM people").fetchone() == (None,)


def test_migration_is_safe_when_initial_schema_already_has_gender():
    migration = importlib.import_module(
        "yaffo.scripts.db.migrations.004_MIGRATION_20260624_add_person_gender"
    )
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT, gender INTEGER)"
    )

    migration.migrate(connection)

    assert [row[1] for row in connection.execute("PRAGMA table_info(people)")].count("gender") == 1
