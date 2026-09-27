"""Finishing the run Job of a task that failed without finishing it.

The task-queue host calls `on_task_failed` whenever it records a task as errored:
its worker crashed (a native crash in the ML code kills only the child), or it
raised an exception its own code didn't handle. Neither path runs the task's
finalizer, and the queue never retries an errored task, so without this the run's
Job would read RUNNING (or Stopping, if cancelled) forever.

Run Jobs are keyed by their task's queue id (job_repository.run_job_id), so the
task id finds the Job. Other tasks' ids match no Job and this is a no-op; chord
batches are accounted for by their chord's callback instead (complete_job).

Runs in the host process, which must never import task modules (it has to survive
the crashes they cause), so this module stays on the db layer.
"""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from yaffo.common import DB_PATH
from yaffo.db.repositories.job_repository import fail_run_job
from yaffo.utils.job_codes import PROBLEM_TASK_ERROR, PROBLEM_WORKER_STOPPED
from yaffo.logging_config import get_logger

logger = get_logger(__name__, "background_tasks")

# Longest error text kept on the Job (a traceback's last line is what matters).
MAX_ERROR_CHARS = 500


def _summary(error: str) -> str:
    lines = [line for line in (error or "").strip().splitlines() if line.strip()]
    return (lines[-1] if lines else "The task failed")[:MAX_ERROR_CHARS]


def _problem(error: str) -> str:
    """Worker crash or exception: the host reports a crash as "worker crashed (…)"."""
    return PROBLEM_WORKER_STOPPED if (error or "").startswith("worker crashed") else PROBLEM_TASK_ERROR


def record_task_failure(engine: Engine, task_id: str, name: str, error: str) -> None:
    """Fail the Job of task `task_id` (if it has one) on `engine`'s database."""
    with Session(engine) as session:
        if fail_run_job(session, task_id, _summary(error), _problem(error)):
            logger.warning(f"task {name}[{task_id}] failed without finishing its Job; marked it ended")


_engine: Engine | None = None


def on_task_failed(task_id: str, name: str, error: str) -> None:
    """The host's hook, bound to the app database."""
    global _engine
    if _engine is None:
        _engine = create_engine(f"sqlite:///{DB_PATH}")
    record_task_failure(_engine, task_id, name, error)


