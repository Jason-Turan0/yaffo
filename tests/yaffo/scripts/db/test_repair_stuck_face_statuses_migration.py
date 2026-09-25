import importlib
import sqlite3

import pytest

pytestmark = pytest.mark.unit


def test_migration_repairs_processing_faces_and_unlinks_ignored_faces():
    migration = importlib.import_module(
        "yaffo.scripts.db.migrations.010_MIGRATION_20260924_repair_stuck_face_statuses"
    )
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE faces (id INTEGER PRIMARY KEY, status TEXT)")
    connection.execute("CREATE TABLE people_face (person_id INTEGER, face_id INTEGER UNIQUE)")
    connection.executemany("INSERT INTO faces (id, status) VALUES (?, ?)", [
        (1, "PROCESSING"),   # stuck, linked       -> ASSIGNED, link kept
        (2, "PROCESSING"),   # stuck, never linked -> UNASSIGNED
        (3, "IGNORED"),      # ignored but linked  -> link removed
        (4, "IGNORED"),      # plain ignored       -> untouched
        (5, "ASSIGNED"),     # healthy             -> untouched
        (6, "UNASSIGNED"),   # healthy             -> untouched
    ])
    connection.executemany("INSERT INTO people_face (person_id, face_id) VALUES (?, ?)",
                           [(7, 1), (7, 3), (7, 5)])

    migration.migrate(connection)

    assert dict(connection.execute("SELECT id, status FROM faces")) == {
        1: "ASSIGNED", 2: "UNASSIGNED", 3: "IGNORED", 4: "IGNORED", 5: "ASSIGNED", 6: "UNASSIGNED",
    }
    assert sorted(connection.execute("SELECT person_id, face_id FROM people_face")) == [(7, 1), (7, 5)]
