"""SQLite-backed durable queue store.

One file (`queue.db`, WAL mode) shared by every process: producers (Flask, the
watcher, worker children running task code) only ever INSERT new READY rows; the
host is the sole mutator of task *status* (ready -> running -> done/error/skipped)
and the sole coordinator of groups/pipelines, which avoids double-dispatch without
cross-process locking. Connections are thread-local so the store is safe to share
across Flask's request threads and the host's single dispatch thread.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

STATUS_READY = "ready"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    args_json TEXT NOT NULL DEFAULT '[]',
    kwargs_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'ready',
    eta REAL,
    lock_name TEXT,
    context INTEGER NOT NULL DEFAULT 0,
    group_id TEXT,
    continuation_json TEXT,
    result_json TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_task_status_eta ON task(status, eta, created_at);

CREATE TABLE IF NOT EXISTS task_group (
    id TEXT PRIMARY KEY,
    callback_json TEXT,
    continuation_json TEXT,
    total INTEGER NOT NULL,
    finished INTEGER NOT NULL DEFAULT 0,
    results_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS task_lock (
    name TEXT PRIMARY KEY,
    holder TEXT,
    acquired_at REAL
);

CREATE TABLE IF NOT EXISTS periodic_state (
    name TEXT PRIMARY KEY,
    last_minute INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS watcher_suppression (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    signature TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_watcher_suppression_path ON watcher_suppression(path);
"""


@dataclass
class TaskRow:
    id: str
    name: str
    args: list
    kwargs: dict
    status: str
    eta: Optional[float]
    lock_name: Optional[str]
    context: bool
    group_id: Optional[str]
    continuation: Optional[list]
    result: Any
    error: Optional[str]

    @staticmethod
    def from_sqlite(row: sqlite3.Row) -> "TaskRow":
        return TaskRow(
            id=row["id"],
            name=row["name"],
            args=json.loads(row["args_json"]),
            kwargs=json.loads(row["kwargs_json"]),
            status=row["status"],
            eta=row["eta"],
            lock_name=row["lock_name"],
            context=bool(row["context"]),
            group_id=row["group_id"],
            continuation=json.loads(row["continuation_json"]) if row["continuation_json"] else None,
            result=json.loads(row["result_json"]) if row["result_json"] is not None else None,
            error=row["error"],
        )


@dataclass
class GroupState:
    id: str
    callback_json: Optional[str]
    continuation_json: Optional[str]
    total: int
    finished: int
    results: list


class Store:
    def __init__(self, filename: str):
        self.filename = filename
        self._local = threading.local()
        self.ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.filename, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def ensure_schema(self) -> None:
        self._conn().executescript(_SCHEMA)

    # ---- producer side: INSERT only --------------------------------------

    def insert_task(
        self,
        name: str,
        args: list,
        kwargs: dict,
        *,
        context: bool = False,
        lock_name: Optional[str] = None,
        eta: Optional[float] = None,
        group_id: Optional[str] = None,
        continuation: Optional[list] = None,
    ) -> str:
        task_id = str(uuid.uuid4())
        self._conn().execute(
            """INSERT INTO task (id, name, args_json, kwargs_json, status, eta,
                                 lock_name, context, group_id, continuation_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id, name, json.dumps(args), json.dumps(kwargs), STATUS_READY,
                eta, lock_name, int(context), group_id,
                json.dumps(continuation) if continuation is not None else None,
                time.time(),
            ),
        )
        return task_id

    def create_group(
        self, callback_json: Optional[str], continuation_json: Optional[str], total: int
    ) -> str:
        group_id = str(uuid.uuid4())
        self._conn().execute(
            """INSERT INTO task_group (id, callback_json, continuation_json, total,
                                       finished, results_json, created_at)
               VALUES (?, ?, ?, ?, 0, '[]', ?)""",
            (group_id, callback_json, continuation_json, total, time.time()),
        )
        return group_id

    # ---- host side: dispatch + coordination ------------------------------

    def fetch_ready(self, now: float, limit: int) -> list[TaskRow]:
        rows = self._conn().execute(
            """SELECT * FROM task
               WHERE status = ? AND (eta IS NULL OR eta <= ?)
               ORDER BY created_at LIMIT ?""",
            (STATUS_READY, now, limit),
        ).fetchall()
        return [TaskRow.from_sqlite(r) for r in rows]

    def mark_running(self, task_id: str) -> None:
        self._conn().execute(
            "UPDATE task SET status=?, started_at=?, attempts=attempts+1 WHERE id=?",
            (STATUS_RUNNING, time.time(), task_id),
        )

    def mark_done(self, task_id: str, result: Any) -> None:
        self._conn().execute(
            "UPDATE task SET status=?, finished_at=?, result_json=? WHERE id=?",
            (STATUS_DONE, time.time(), json.dumps(result), task_id),
        )

    def mark_error(self, task_id: str, error: str) -> None:
        self._conn().execute(
            "UPDATE task SET status=?, finished_at=?, error=? WHERE id=?",
            (STATUS_ERROR, time.time(), error, task_id),
        )

    def mark_skipped(self, task_id: str) -> None:
        self._conn().execute(
            "UPDATE task SET status=?, finished_at=? WHERE id=?",
            (STATUS_SKIPPED, time.time(), task_id),
        )

    def requeue_running(self) -> int:
        """On host startup, return tasks stranded in RUNNING by a previous crash
        to READY (at-least-once). Also clears any held locks."""
        cur = self._conn().execute(
            "UPDATE task SET status=? WHERE status=?", (STATUS_READY, STATUS_RUNNING)
        )
        self._conn().execute("DELETE FROM task_lock")
        return cur.rowcount

    def add_group_result(self, group_id: str, result: Any) -> GroupState:
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT * FROM task_group WHERE id=?", (group_id,)).fetchone()
            results = json.loads(row["results_json"])
            results.append(result)
            finished = row["finished"] + 1
            conn.execute(
                "UPDATE task_group SET finished=?, results_json=? WHERE id=?",
                (finished, json.dumps(results), group_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return GroupState(
            id=group_id,
            callback_json=row["callback_json"],
            continuation_json=row["continuation_json"],
            total=row["total"],
            finished=finished,
            results=results,
        )

    # ---- locks (lock_task) ----------------------------------------------

    def try_acquire_lock(self, name: str, holder: str) -> bool:
        try:
            self._conn().execute(
                "INSERT INTO task_lock (name, holder, acquired_at) VALUES (?, ?, ?)",
                (name, holder, time.time()),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def release_lock(self, name: str) -> None:
        self._conn().execute("DELETE FROM task_lock WHERE name=?", (name,))

    # ---- watcher self-write suppression ---------------------------------
    # Loop-guard companion (Mechanism 2, see docs/development/automations.md): when yaffo writes a
    # file it also watches, it records the write here so the watcher ignores the OS
    # event it caused — breaking a write -> reindex -> photo_indexed -> automation loop
    # the in-memory causal chain can't see (the write re-enters via a separate process).

    def add_suppression(self, path: str, signature: str) -> None:
        """Record that yaffo just wrote `path`, leaving it with `signature`
        (e.g. "size:mtime_ns"). The watcher consumes a matching entry instead of
        re-indexing."""
        self._conn().execute(
            "INSERT INTO watcher_suppression (path, signature, created_at) VALUES (?, ?, ?)",
            (path, signature, time.time()),
        )

    def consume_suppression(self, path: str, signature: str, max_age: float) -> bool:
        """If a non-expired self-write for (`path`, `signature`) is recorded, delete one
        such row and return True (the watcher should skip this event). Otherwise False —
        a genuine external edit has a different signature and is not suppressed.
        Matching by exact signature makes a wrong suppression of a real edit
        near-impossible; the delete makes each self-write suppress at most one event."""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cutoff = time.time() - max_age
            row = conn.execute(
                """SELECT id FROM watcher_suppression
                   WHERE path = ? AND signature = ? AND created_at >= ?
                   ORDER BY created_at LIMIT 1""",
                (path, signature, cutoff),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return False
            conn.execute("DELETE FROM watcher_suppression WHERE id = ?", (row["id"],))
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def sweep_suppressions(self, max_age: float) -> int:
        """Delete suppression rows older than `max_age` seconds (so a crash between
        write and detection can't leave a stale entry that mutes a later real edit).
        Returns the number swept."""
        cur = self._conn().execute(
            "DELETE FROM watcher_suppression WHERE created_at < ?",
            (time.time() - max_age,),
        )
        return cur.rowcount

    # ---- periodic single-fire -------------------------------------------

    def claim_periodic_minute(self, name: str, minute: int) -> bool:
        """Atomically record that `name` fired for `minute`; returns False if it
        already fired this minute (so the host never double-enqueues a tick)."""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT last_minute FROM periodic_state WHERE name=?", (name,)
            ).fetchone()
            if row is not None and row["last_minute"] >= minute:
                conn.execute("COMMIT")
                return False
            conn.execute(
                """INSERT INTO periodic_state (name, last_minute) VALUES (?, ?)
                   ON CONFLICT(name) DO UPDATE SET last_minute=excluded.last_minute""",
                (name, minute),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise
