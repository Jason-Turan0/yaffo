"""Mark faces that are linked to a person but still UNASSIGNED as ASSIGNED.

The auto_assign_faces automation (and the sandbox assign_faces action) created the
person link without updating the face's status, so matched faces kept appearing on
the Unassigned Faces screen, where assigning them again was rejected. The linking
code now sets the status; this repairs the rows it left behind.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE faces SET status = 'ASSIGNED' "
        "WHERE status = 'UNASSIGNED' "
        "AND id IN (SELECT face_id FROM people_face)"
    )
