"""The host (master) process: scheduler + dispatcher + coordinator + supervisor.

The host owns the queue. It never runs task code and never imports the task
modules, so it can never load dlib and can never segfault -- which is the whole
point: it stays alive to recover crashed children. Responsibilities:

- **Scheduler**: each minute, enqueue due periodic tasks (single-fire); delayed
  tasks become claimable when their `eta` passes (handled by the claim query).
- **Dispatcher**: claim READY rows (the host is the sole status mutator, so a
  single dispatch thread is race-free), honour named locks, hand each to a free
  worker.
- **Coordinator**: on a leaf finishing, advance chord groups / pipeline
  continuations (`taskq.core.on_task_finished`).
- **Supervisor**: detect a child that exited (incl. SIGSEGV) and respawn it; a
  crash mid-task is recorded and the composition is still advanced so a poisoned
  batch can't wedge a chord forever.
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import queue as _queue
import signal
import time
from typing import Any, Optional

from yaffo.taskq.core import on_task_finished
from yaffo.taskq.cron import CronSpec
from yaffo.taskq.store import Store, TaskRow
from yaffo.taskq.worker import DEFAULT_BOOTSTRAP, DEFAULT_QUEUE_REF, DONE, worker_main
from yaffo.logging_config import get_logger
from yaffo.site_agents import llm_config

logger = get_logger(__name__, "background_tasks")

POLL_INTERVAL = 0.1


class WorkerHandle:
    def __init__(self, worker_id: int, proc: mp.Process, inbox: "mp.Queue"):
        self.id = worker_id
        self.proc = proc
        self.inbox = inbox
        self.busy_task: Optional[TaskRow] = None
        self.lock_name: Optional[str] = None


class Host:
    def __init__(
        self,
        filename: str,
        periodic: list[tuple[str, CronSpec]],
        num_workers: int,
        max_tasks_per_worker: int,
        bootstrap: str = DEFAULT_BOOTSTRAP,
        queue_ref: str = DEFAULT_QUEUE_REF,
    ):
        self.store = Store(filename)
        self.periodic = periodic
        self.num_workers = num_workers
        self.max_tasks_per_worker = max_tasks_per_worker
        self.bootstrap = bootstrap
        self.queue_ref = queue_ref
        self.ctx = mp.get_context("spawn")
        self.outbox: "mp.Queue" = self.ctx.Queue()
        self.workers: dict[int, WorkerHandle] = {}
        self.running = True

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)
        self.setup()
        self._loop()

    def setup(self) -> None:
        """Recover stranded tasks and bring up the worker pool (no signal
        handlers, so it's reusable from a test thread)."""
        requeued = self.store.requeue_running()
        if requeued:
            logger.info(f"requeued {requeued} task(s) stranded by a previous host")
        for i in range(self.num_workers):
            self._spawn(i)
        logger.info(
            f"taskq host up: {self.num_workers} spawn worker(s), "
            f"recycle every {self.max_tasks_per_worker} task(s); "
            f"periodic: {[name for name, _ in self.periodic] or 'none'}"
        )

    def _on_signal(self, signum, frame) -> None:
        logger.info(f"signal {signum} received; shutting down")
        self.running = False

    def _spawn(self, worker_id: int) -> None:
        # Publish the API key into our env first so this spawn child inherits it and
        # never reads the keychain itself (which would pop an OS prompt in a background
        # process). A no-op until a key is configured; cheap to retry on every respawn.
        llm_config.prime_subprocess_env()
        inbox: "mp.Queue" = self.ctx.Queue()
        proc = self.ctx.Process(
            target=worker_main,
            args=(
                inbox, self.outbox, worker_id, self.max_tasks_per_worker,
                self.bootstrap, self.queue_ref,
            ),
            daemon=True,
        )
        proc.start()
        self.workers[worker_id] = WorkerHandle(worker_id, proc, inbox)

    def run_once(self) -> None:
        """A single supervision pass: reap results, recover crashes, schedule
        periodics, dispatch ready work."""
        self._collect_results()
        self._check_crashes()
        self._tick_periodic()
        self._dispatch()

    def _loop(self) -> None:
        while self.running:
            self.run_once()
            time.sleep(POLL_INTERVAL)
        self.shutdown()

    # ---- steps ----------------------------------------------------------

    def _collect_results(self) -> None:
        while True:
            try:
                worker_id, task_id, status, payload = self.outbox.get_nowait()
            except _queue.Empty:
                break
            w = self.workers.get(worker_id)
            row = w.busy_task if w else None
            if w:
                if w.lock_name:
                    self.store.release_lock(w.lock_name)
                    w.lock_name = None
                w.busy_task = None
            if row is None:
                continue
            # A result/store error must never take down the supervisor; record the
            # task as failed and keep going. (Non-JSON returns are already caught
            # in the worker and arrive here as ERROR, so this is belt-and-braces.)
            try:
                if status == DONE:
                    self.store.mark_done(task_id, payload)
                    self._advance(row, payload)
                else:
                    logger.error(f"task {row.name}[{task_id}] failed:\n{payload}")
                    self.store.mark_error(task_id, str(payload))
                    self._advance(row, None)
            except Exception:
                logger.exception(f"failed to record result for {row.name}[{task_id}]")
                try:
                    self.store.mark_error(task_id, "host failed to record result")
                except Exception:
                    logger.exception(f"could not even mark {task_id} errored")

    def _check_crashes(self) -> None:
        for worker_id, w in list(self.workers.items()):
            if w.proc.is_alive():
                continue
            if w.busy_task is not None:
                row = w.busy_task
                logger.error(
                    f"worker {worker_id} died (exitcode={w.proc.exitcode}) running "
                    f"{row.name}[{row.id}]; recording failure and advancing"
                )
                self.store.mark_error(row.id, f"worker crashed (exitcode={w.proc.exitcode})")
                if w.lock_name:
                    self.store.release_lock(w.lock_name)
                self._advance(row, None)
            del self.workers[worker_id]
            self._spawn(worker_id)

    def _advance(self, row: TaskRow, result: Any) -> None:
        try:
            on_task_finished(self.store, row, result)
        except Exception:
            logger.exception(f"coordinator failed advancing {row.name}[{row.id}]")

    def _tick_periodic(self) -> None:
        minute = int(time.time()) // 60
        for name, _cron in self.periodic:
            if self.store.claim_periodic_minute(name, minute):
                self.store.insert_task(name, [], {})
                logger.debug(f"enqueued periodic task {name} for minute {minute}")

    def _dispatch(self) -> None:
        free = [w for w in self.workers.values() if w.busy_task is None and w.proc.is_alive()]
        if not free:
            return
        for row in self.store.fetch_ready(time.time(), limit=len(free)):
            if not free:
                break
            if row.lock_name and not self.store.try_acquire_lock(row.lock_name, row.id):
                logger.debug(f"lock '{row.lock_name}' held; skipping {row.name}[{row.id}]")
                self.store.mark_skipped(row.id)
                continue
            w = free.pop()
            self.store.mark_running(row.id)
            w.busy_task = row
            w.lock_name = row.lock_name
            w.inbox.put((row.id, row.name, row.args, row.kwargs, row.context))

    def shutdown(self) -> None:
        logger.info("draining workers")
        for w in self.workers.values():
            try:
                w.inbox.put(None)
            except Exception:
                pass
        deadline = time.time() + 5
        for w in self.workers.values():
            remaining = max(0, deadline - time.time())
            w.proc.join(timeout=remaining)
            if w.proc.is_alive():
                w.proc.terminate()
        logger.info("taskq host stopped")


def main() -> None:
    import os
    from yaffo.background_tasks.periodic import PERIODIC_TASKS
    from yaffo.common import QUEUE_DB_PATH
    from yaffo.config import get_int as get_config_int

    # Defaults from config.toml ([tasks] workers / recycle); a CLI flag still wins
    # over the config value, which wins over the built-in fallback.
    parser = argparse.ArgumentParser(description="Run the yaffo task queue host")
    parser.add_argument(
        "-w", "--workers", type=int,
        default=get_config_int("tasks", "workers", os.cpu_count() or 4),
    )
    parser.add_argument(
        "-r", "--recycle", type=int,
        default=get_config_int("tasks", "recycle", 100),
        help="recycle each worker after this many tasks (caps native memory)",
    )
    args = parser.parse_args()

    Host(
        filename=str(QUEUE_DB_PATH),
        periodic=PERIODIC_TASKS,
        num_workers=args.workers,
        max_tasks_per_worker=args.recycle,
    ).start()


if __name__ == "__main__":
    main()
