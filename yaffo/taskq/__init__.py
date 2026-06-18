"""A small, purpose-built task queue: a SQLite-durable queue plus a host process
that supervises a pool of spawn-started worker children. It replaces Huey, whose
process workers are unusable on macOS/Windows and whose thread workers segfault our
parallel dlib indexing. See docs/task-queue-migration.md."""
from yaffo.taskq.core import Result, Task, TaskContext, TaskQueue
from yaffo.taskq.cron import CronSpec, crontab
from yaffo.taskq.signatures import Pipeline, Signature, chord

__all__ = [
    "TaskQueue",
    "Task",
    "TaskContext",
    "Result",
    "crontab",
    "CronSpec",
    "chord",
    "Signature",
    "Pipeline",
]
