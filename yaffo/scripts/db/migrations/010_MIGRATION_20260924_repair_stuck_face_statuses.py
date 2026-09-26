"""Repair faces left in states no current code path produces.

- PROCESSING faces: the assign route marks faces PROCESSING and queues
  assign_faces_to_person, which sets them ASSIGNED. When that task failed it
  swallowed the exception, so the queue recorded it as done and the faces stayed
  PROCESSING forever -- hidden from the Unassigned Faces screen and never assigned.
  (The task now resets them and reports the failure.) A PROCESSING face that is
  linked to a person is treated as ASSIGNED; one with no link goes back to
  UNASSIGNED so it can be assigned again.
- IGNORED faces with a person link: auto-assign used to link faces without setting
  their status, so linked faces still showed as unassigned and some were then
  ignored. The ignore is the later, deliberate choice, so the link is removed.

Runs at startup before the task worker, so no assignment is in flight; a queued
assign task that does run later re-links and marks its faces ASSIGNED regardless.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE faces SET status = 'ASSIGNED' "
        "WHERE status = 'PROCESSING' "
        "AND id IN (SELECT face_id FROM people_face)"
    )
    conn.execute(
        "UPDATE faces SET status = 'UNASSIGNED' "
        "WHERE status = 'PROCESSING'"
    )
    conn.execute(
        "DELETE FROM people_face "
        "WHERE face_id IN (SELECT id FROM faces WHERE status = 'IGNORED')"
    )
