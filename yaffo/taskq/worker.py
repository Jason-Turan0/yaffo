"""Spawn-started worker child.

A fresh interpreter per child (no fork) so concurrent dlib calls are isolated and
crash-contained: a segfault kills only this child, and the host respawns it. The
child imports the task registry (dlib loads *here*, never in the host), pulls
assignments off its inbox queue, runs the task function, and reports the result or
exception back on the shared outbox. After `max_tasks` it exits cleanly so the host
recycles it, capping native memory growth.

`bootstrap` is the module the child imports to register every task (the app's is
`yaffo.background_tasks.tasks`); `queue_ref` (``module:attr``) locates the shared
TaskQueue object. Both are parameters so the pool can be exercised in tests with a
throwaway registry.
"""
from __future__ import annotations

import importlib
import multiprocessing as _mp
import traceback
from typing import Any

# Outbox message kinds (worker -> host)
DONE = "done"
ERROR = "error"

DEFAULT_BOOTSTRAP = "yaffo.background_tasks.tasks"
DEFAULT_QUEUE_REF = "yaffo.background_tasks.config:task_queue"


def worker_main(
    inbox: "_mp.Queue",
    outbox: "_mp.Queue",
    worker_id: int,
    max_tasks: int,
    bootstrap: str = DEFAULT_BOOTSTRAP,
    queue_ref: str = DEFAULT_QUEUE_REF,
) -> None:
    importlib.import_module(bootstrap)  # side effect: registers every @task
    module_name, attr = queue_ref.split(":")
    task_queue = getattr(importlib.import_module(module_name), attr)
    from yaffo.taskq.core import TaskContext, assert_json_result
    from yaffo.logging_config import get_logger

    logger = get_logger(__name__, "background_tasks")
    logger.info(f"worker {worker_id} started (max_tasks={max_tasks})")

    processed = 0
    while processed < max_tasks:
        msg = inbox.get()
        if msg is None:  # graceful shutdown sentinel
            break
        task_id, name, args, kwargs, context = msg
        try:
            task = task_queue.registry[name]
            call_kwargs = dict(kwargs)
            if context:
                call_kwargs["task"] = TaskContext(id=task_id)
            result: Any = task.fn(*args, **call_kwargs)
            assert_json_result(name, result)  # bad return -> reported as a failure below
            outbox.put((worker_id, task_id, DONE, result))
        except Exception:
            outbox.put((worker_id, task_id, ERROR, traceback.format_exc()))
        processed += 1

    logger.info(f"worker {worker_id} exiting after {processed} task(s)")
