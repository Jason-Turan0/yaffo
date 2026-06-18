# Task Queue Migration: Huey → roll-your-own (master/child processes)

**Status:** Implemented · **Created:** 2026-06-18 · **Completed:** 2026-06-18

The replacement lives in `yaffo/taskq/` (`signatures.py` composition primitives,
`store.py` SQLite queue, `core.py` `TaskQueue`/coordinator, `host.py` host process,
`worker.py` spawn child, `cron.py` minute cron). The process-wide queue object is
`yaffo.background_tasks.config.task_queue`; periodic tasks the host schedules are
declared in `yaffo/background_tasks/periodic.py`. Run the host with
`python -m yaffo.taskq.host` (or `inv start-tasks` / `inv app-local`). Tests:
immediate-mode composition spec `tests/yaffo/utils/test_index_jobs.py`, persistent
coordinator `tests/yaffo/taskq/test_coordinator.py`, and spawn-pool + crash
isolation `tests/yaffo/taskq/test_host_spawn.py`. Huey is removed from
`pyproject.toml`; the `job_results.huey_task_id` column was renamed to `task_id`.

## 1. Why

Huey's process workers are unusable on our target platforms, and thread workers
crash our workload:

- **Thread workers (`-k thread`)** — the indexing tasks call `face_recognition`
  (dlib), which releases the GIL and runs native code in parallel across worker
  threads. With >1 worker this **segfaults** (confirmed: `index_photo` across 4
  threads → exit 139 SIGSEGV; 1 thread → clean). We're currently pinned to
  `-w 1`, i.e. single-threaded indexing.
- **Process workers (`-k process`)** — broken on macOS/Windows with Huey 3.0:
  - `spawn` (macOS/Windows default): Huey hands a non-picklable local closure
    (`Consumer._create_process.<locals>._run`) to `multiprocessing.Process` →
    `AttributeError` at startup. This is a **regression in 3.0** (2.3.0 set the
    start method to `fork` on macOS; 3.0.x dropped it; fixed in unreleased
    `master`). See GitHub issue #551.
  - forcing `fork`: workers **segfault** in `os_log`/SQLite (`openDatabase` →
    `pysqlite_connection_init`) — macOS fork-without-exec is not safe once native
    libs are loaded.

The root requirement: **run CPU-bound native (dlib) work in parallel, isolated
in separate `spawn`-started processes, with no external broker and working on
macOS + Windows.** No off-the-shelf embedded queue gives us that today (Huey is
fork/thread-bound here; Celery/RQ/Dramatiq need a broker; Procrastinate needs
Postgres). Hence: a small purpose-built queue we control.

## 2. Goals / non-goals

**Goals**
- Durable, SQLite-backed queue (no external broker), shared between the Flask
  producer and the worker host, surviving restarts.
- A **master (host) process** that owns the queue + scheduler and supervises a
  pool of **`spawn`-started child worker processes**.
- True multi-core indexing: N children run dlib concurrently, in isolation; a
  child segfault kills only that child and is recovered, never the host.
- Preserve the existing task **call sites and semantics** (chords, pipelines,
  periodic dispatch, locks, delayed tasks, cooperative cancellation) so the rest
  of the app changes as little as possible.
- Keep an **immediate/synchronous** mode for tests.

**Non-goals**
- Distributed/multi-machine execution. Single host only.
- Features we don't currently use: automatic retries, task priorities, task/result
  expiration, revoke/is_revoked, greenlet workers. (Cancellation stays cooperative
  via `Job.status`.)
- Replacing the `Job` / `JobResult` DB models — those are the app's progress
  source of truth and stay as-is.

## 3. Current Huey usage (the contract to preserve)

Inventory of every Huey feature in use and where. This is the acceptance surface
for the replacement.

| # | Huey feature | Used at | Must-have? |
|---|---|---|---|
| 1 | `SqliteHuey(filename, immediate, utc)` durable store | `background_tasks/config.py` | ✅ |
| 2 | `@huey.task()` (calling fn = enqueue) | ~18 tasks under `background_tasks/tasks/` | ✅ |
| 3 | `@huey.task(context=True)` → `task.id` | `find_duplicates.py` (stored as `JobResult.huey_task_id`) | ✅ |
| 4 | `@huey.periodic_task(crontab(minute='*'))` | `dispatcher.py` (the only periodic task; drives schedule automations) | ✅ |
| 5 | `@huey.lock_task('file-sync')` (skip if already running) | `file_sync.py` | ✅ |
| 6 | `task.schedule(args=, delay=)` (ETA/delayed) | `utils.py:schedule_job_completion` → `complete_job_task` | ✅ |
| 7 | `task.s(args)` (signature/partial) | `index_stage.py`, `index_jobs.py` | ✅ |
| 8 | `chord(members, callback.s())` (group + barrier) | `index_jobs.py` (import), `index_stage.py` (index) | ✅ |
| 9 | `.then(task, args)` (pipeline chain, prev result appended) | `index_jobs.py` | ✅ |
| 10 | `huey.enqueue(pipeline)` | `index_jobs.py`, `index_stage.py` | ✅ |
| 11 | Result passing: prev step result appended to next args; chord appends member results to callback | `start_index_stage(prev_result=None)`, `complete_job_callback(results=None)` | ✅ (positional contract; values currently ignored) |
| 12 | `immediate=True` (synchronous, in-process) | `tests/yaffo/utils/test_index_jobs.py` | ✅ |
| 13 | Consumer `huey_consumer -w N -k TYPE` + scheduler | `tasks.py`, run as separate process | ✅ (replaced by host) |
| 14 | Huey result store | internal chord/pipeline coordination only; app never reads it (`.get()` only in `main.py` demo) | ⚠️ internal only |
| 15 | UTC timestamps | `config.py utc=True`; `dispatcher` uses `datetime.utcnow()` | ✅ |

**Producers (enqueue sites)** — all rely on "calling the decorated task enqueues it":
- Routes: `generate_theme_task(...)` (`themes_page.py`), `generate_page_task(...)`
  (`pages.py`), `generate_automation_task(...)`, `find_duplicates_task(...)` /
  `remove_duplicates_task(...)` (`remove_duplicates.py`).
- `emit_event(...)` → enqueues `dispatch_event_task` (`events.py`).
- `invoke_automation(...)` → calls a handler that enqueues the system task
  (`automation_dispatch.py`, `registry.py`, per-automation `enqueue_*`).
- File watcher process → `enqueue_index_jobs(...)` (`utils/index_jobs.py`).

**Not used (safe to drop):** retries, priorities, expiration, revoke, greenlet
workers, reading results out of the store.

**Tasks (18):** import_photo, index_photo, index_stage(start_index_stage),
complete_job (complete_job_callback / finalize_job_task / complete_job_task),
dispatch_event, find_duplicates, remove_duplicates, generate_page, generate_theme,
generate_automation, run_automation(run_automation_code_task), file_sync,
auto_assign_faces, duplicate_scan, export_photo_tag, assign_location_name,
geotag_from_neighbors, dispatcher(dispatch_scheduled_tasks).

## 4. Target architecture

```
┌────────────────────────── Flask app (producer) ──────────────────────────┐
│  enqueue(task, args)  ──writes a task row──►  queue.db (SQLite, WAL)       │
└───────────────────────────────────────────────────────────────────────────┘
                                   │ (same SQLite file)
┌────────────────────────── Host / master process ─────────────────────────┐
│  • Scheduler loop: due periodic tasks (cron) + delayed/ETA tasks           │
│  • Dispatcher: atomically claim READY task rows, assign to a free child    │
│  • Coordinator: chord barriers + pipeline continuations + result passing   │
│  • Lock manager: named locks (lock_task)                                   │
│  • Supervisor: spawn pool, health-check, restart dead children             │
└───────────────────────────────────────────────────────────────────────────┘
        │ assign (IPC)            ▲ result/exception (IPC)
        ▼                         │
┌──────────── child workers (multiprocessing spawn, N processes) ───────────┐
│  fresh interpreter → import task registry → run task fn in isolation       │
│  dlib runs here, one call per process → safe concurrency, crash-isolated   │
└───────────────────────────────────────────────────────────────────────────┘
```

Key decisions:
- **Children are `spawn`-started** (`multiprocessing.get_context("spawn")`), the
  same model `index_photos_batch` already uses successfully. No fork → no macOS
  fork-safety crash. Separate address spaces → concurrent dlib is safe; a segfault
  is contained and the supervisor respawns.
- **The host does no task work** — it only schedules/dispatches/coordinates. It
  must never import dlib so a crash there is impossible and respawn is cheap.
- **SQLite is the durable queue** (WAL mode, `busy_timeout`). Producer and host
  are separate OS processes writing the same file (as today). Task claiming is a
  single atomic `UPDATE ... WHERE status='ready' ... RETURNING` (or guarded
  `UPDATE` + rowcount) to avoid double-dispatch.
- **Long-lived children** pulling from an in-memory assignment queue (avoid
  per-task spawn cost), recycled after K tasks to cap native memory growth.

## 5. Feature checklist (implementation work)

### A. Core queue + persistence
- [ ] `queue.db` schema: `task(id, name, args_json, status, eta, lock_name, group_id, pipeline_id, position, result_json, error, created_at, started_at, finished_at, attempts)`.
- [ ] WAL + `busy_timeout` + a single writer discipline from the host; producers only INSERT.
- [ ] Atomic claim of the next READY task (`status=ready AND (eta IS NULL OR eta<=now)`), respecting lock availability, ordered by created_at.
- [ ] Serialization contract for args/results (JSON; document that args must be JSON-serializable — today they're str/int/list, no objects).
- [ ] Requeue/recover tasks left in `running` by a host crash on startup (at-least-once; document idempotency expectations — tasks already re-read `Job` state).

### B. Task definition & registration
- [ ] `@task()` decorator: registers fn in a name→fn registry; calling the fn **enqueues** (INSERT) and returns a lightweight result handle.
- [ ] `task.s(*args)` signature object (deferred call) for composing chords/pipelines.
- [ ] `task.schedule(args=, delay=)` → INSERT with `eta = now + delay`.
- [ ] `context=True`: pass a context object exposing `.id` (our task row id) to the fn — replaces `find_duplicates_task(task=…)`/`JobResult.huey_task_id`.
- [ ] Registry import module the children load on spawn (equivalent of `main.py` importing all tasks).

### C. Composition (the hard part)
- [x] **Group + chord**: enqueue N members with a shared `group_id`; when the last member finishes, enqueue the callback once with the members' results. **Empty group = immediately done** (changed from Huey, which never fired the callback): the callback fires once with an empty results list and the continuation runs, so the app no longer special-cases empty import/index stages (`finalize_job_task` removed; `index_jobs`/`index_stage` use one uniform chord path).
- [ ] **Pipeline / `.then()`**: on a step finishing, enqueue the next step with `prev_result` appended to its args (matches `start_index_stage(index_job_id, prev_result=None)`).
- [ ] **chord-then-pipeline**: reproduce `chord(members, cb).then(start_index_stage, index_job_id)` and `finalize_job_task.s(id).then(start_index_stage, id)` from `index_jobs.py`.
- [ ] `enqueue(pipeline)` entry point that persists a whole composed graph atomically.
- [ ] Result passing semantics: positional append, and chord callback receives the list of member results (currently ignored, but preserve the signature).

### D. Scheduling
- [ ] `@periodic_task(cron)` registration + a scheduler tick (≤60s) that enqueues due periodic tasks. Only one exists today: `dispatch_scheduled_tasks` every minute — a minimal minute-granular cron evaluator suffices (or special-case "every minute").
- [ ] Delayed/ETA dispatch: scheduler promotes `eta<=now` tasks to READY.
- [ ] Single-fire guarantee for periodic tasks (no double-enqueue if a tick is slow).

### E. Concurrency & process management
- [ ] `spawn` worker pool of N children (configurable; default = CPU count for indexing).
- [ ] Assignment IPC (host→child) and result/exception IPC (child→host).
- [ ] **Crash isolation**: detect child exit (incl. SIGSEGV exit 139) via process exit code; mark the in-flight task failed/requeued; **respawn** the child. (Directly fixes the dlib crash blast radius.)
- [ ] Worker recycling after K tasks (cap dlib/native memory).
- [ ] Exception capture: serialize child exceptions back to the host (record on the task row); host logs, never dies.

### F. Locks
- [ ] `@lock_task(name)` / named locks: a task holding lock `name` causes others with the same lock to **skip** (no-op return), matching Huey's `lock_task` used by `file_sync` (slow scans must not pile up).

### G. Producer / app integration
- [ ] Drop-in `enqueue` so existing call sites (`generate_theme_task(...)`, `emit_event`, `invoke_automation` handlers, `enqueue_index_jobs`) keep working with minimal edits.
- [ ] Cooperative cancellation unchanged: tasks keep polling `get_job_status(job_id)`; no queue-level revoke needed.
- [ ] Keep `Job` / `JobResult` writes exactly as today (progress counts, results).

### H. Test & dev ergonomics
- [ ] **Immediate mode**: a flag (env or config) that runs `enqueue`/pipelines/chords **synchronously in-process**, so `tests/yaffo/utils/test_index_jobs.py` (which sets `huey.immediate = True` and drives the real pipeline) keeps working with an equivalent switch.
- [ ] Replace `inv start-tasks` to launch the host process; update `inv app-local` (Flask + host + watcher).
- [ ] Structured logging to `background_tasks.log` (host + per-child), including registered-task listing on startup (parity with current consumer output).
- [ ] Graceful shutdown on SIGINT/SIGTERM: stop accepting, drain or requeue in-flight, join children.

### I. Cutover
- [ ] Implement behind a feature switch; run new host alongside Huey off.
- [ ] Migrate task decorators module-by-module (registry shim can wrap both).
- [ ] Verify the full import→index chord pipeline end-to-end (large library) with N spawn workers and no segfaults.
- [ ] Remove `huey` dependency from `pyproject.toml`, delete `config.py`/`main.py` Huey bits, update `docs/automations.md` references.

## 6. Risks & open questions

- **At-least-once vs at-most-once.** A host/child crash mid-task means the task
  may re-run on recovery. Tasks are largely idempotent (re-read `Job`/`Photo`
  state), but confirm import/index/remove-duplicates tolerate replay. Decide the
  default and document it.
- **SQLite write contention.** Producer (Flask, possibly multiple threads) +
  host writing one file. WAL + `busy_timeout` + host-as-sole-mutator-of-status
  should suffice at our scale; validate.
- **Composition complexity.** Chords + pipelines + the empty-group special case
  are the main implementation risk. Strong unit tests required (port
  `test_index_jobs.py` first as the spec).
- **Windows.** `spawn` works on Windows, but validate child IPC, signal handling,
  and SQLite file locking there too (Windows is a stated target).
- **Args serialization.** Everything enqueued must be JSON-safe. Audit args (today
  they're ids/paths/lists — fine) and enforce it at `enqueue`.
- **Scheduler scope.** We only need minute-granular periodic + delayed tasks; do
  not build a full crontab engine unless a real need appears (`compute_next_run`
  already handles automation cron expressions at the app layer).

## 7. References

- Findings on the Huey crash (this repo's investigation): thread-dlib SIGSEGV;
  process-worker spawn/fork failures; macOS `os_log` fork-safety.
- Huey CHANGELOG — process-worker start-method regression/fix.
- Existing spawn-based precedent: `yaffo/utils/index_photos.py:index_photos_batch`
  (`ProcessPoolExecutor`), the model for the child pool.
