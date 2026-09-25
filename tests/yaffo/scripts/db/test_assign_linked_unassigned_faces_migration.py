import importlib
import sqlite3

import pytest

pytestmark = pytest.mark.unit


def test_migration_assigns_only_linked_unassigned_faces():
    migration = importlib.import_module(
        "yaffo.scripts.db.migrations.009_MIGRATION_20260924_assign_linked_unassigned_faces"
    )
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE faces (id INTEGER PRIMARY KEY, status TEXT)")
    connection.execute("CREATE TABLE people_face (person_id INTEGER, face_id INTEGER UNIQUE)")
    connection.executemany("INSERT INTO faces (id, status) VALUES (?, ?)", [
        (1, "UNASSIGNED"),   # linked by auto-assign, status never updated -> repaired
        (2, "UNASSIGNED"),   # genuinely unassigned -> untouched
        (3, "PROCESSING"),   # linked; left for separate handling -> untouched
        (4, "IGNORED"),      # linked; the user's ignore stands -> untouched
        (5, "ASSIGNED"),
    ])
    connection.executemany("INSERT INTO people_face (person_id, face_id) VALUES (?, ?)",
                           [(1, 1), (1, 3), (1, 4), (1, 5)])

    migration.migrate(connection)

    assert dict(connection.execute("SELECT id, status FROM faces")) == {
        1: "ASSIGNED", 2: "UNASSIGNED", 3: "PROCESSING", 4: "IGNORED", 5: "ASSIGNED",
    }
