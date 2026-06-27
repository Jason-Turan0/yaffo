"""A small, purpose-built task queue: a SQLite-durable queue plus a host process
that supervises a pool of spawn-started worker children. Native ML inference runs
in those child processes so worker crashes stay isolated. See
docs/development/task-queue.md."""
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
