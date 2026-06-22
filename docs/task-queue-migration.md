# Task Queue Migration: Huey → roll-your-own (master/child processes)

**Status:** Implemented · **Created:** 2026-06-18 · **Completed:** 2026-06-18 ·
**Doc reconciled with shipped code:** 2026-06-21 (Section 5 checked off, Section 6
risks annotated with outcomes).

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

> **Note (2026-06-21): the face stack has since moved off dlib.** Detection +
> recognition now run on **InsightFace (SCRFD + ArcFace, via `onnxruntime`)** — dlib
> was both slower *and* less accurate (`benchmarks/face/README.md`: ~20× slower
> detection, 63% → 85% exact count; recognition AUC 0.848 → 0.993, EER 20% → 5%). The
> dlib thread-segfault below is the **historical trigger** for this migration; the
> process-isolation architecture is unchanged and still applies, because `onnxruntime`
> is likewise CPU-bound native code best run in isolated, crash-contained spawn
> children. The forward-looking architecture (§2, §4) is written in terms of "native
> ML inference," which now means InsightFace/onnxruntime.

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

The root requirement: **run CPU-bound native ML inference in parallel, isolated
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
- True multi-core indexing: N children run native ML inference concurrently, in
  isolation; a child segfault kills only that child and is recovered, never the host.
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
│  native ML runs here → safe concurrency, crash-isolated                    │
└───────────────────────────────────────────────────────────────────────────┘
```

Key decisions:
- **Children are `spawn`-started** (`multiprocessing.get_context("spawn")`), the
  same model `index_photos_batch` already uses successfully. No fork → no macOS
  fork-safety crash. Separate address spaces → concurrent native inference is safe; a
  segfault is contained and the supervisor respawns.
- **The host does no task work** — it only schedules/dispatches/coordinates. It
  must never import the native ML stack so a crash there is impossible and respawn is
  cheap.
- **SQLite is the durable queue** (WAL mode, `busy_timeout`). Producer and host
  are separate OS processes writing the same file (as today). Task claiming is a
  single atomic `UPDATE ... WHERE status='ready' ... RETURNING` (or guarded
  `UPDATE` + rowcount) to avoid double-dispatch.
- **Long-lived children** pulling from an in-memory assignment queue (avoid
  per-task spawn cost), recycled after K tasks to cap native memory growth.

## 5. Feature checklist (as built)

All shipped. A few items landed differently than first sketched — annotated inline.

### A. Core queue + persistence
- [x] `queue.db` schema — **landed as** `task(id, name, args_json, kwargs_json,
  status, eta, lock_name, context, group_id, continuation_json, result_json, error,
  created_at, started_at, finished_at, attempts)` + index on `(status, eta,
  created_at)`. Diverged from the sketch: **`kwargs_json`** (tasks take kwargs) and a
  **`context`** flag were added; the planned `pipeline_id, position` columns were
  **replaced by `continuation_json`** (per-task continuation, see C). Plus four
  supporting tables: `task_group` (chord barrier state), `task_lock` (named locks),
  `periodic_state` (single-fire minute claim), and `watcher_suppression` (the file
  watcher's self-write loop-guard, which rides in the queue DB).
- [x] WAL + `busy_timeout` (Python `sqlite3 timeout=30`) + host-as-sole-status-mutator;
  producers only INSERT.
- [x] Claim of the next READY task (`status=ready AND (eta IS NULL OR eta<=now)`,
  lock-aware, ordered by `created_at`). **Landed as** a single dispatcher thread doing
  `fetch_ready` → `mark_running` rather than `UPDATE … RETURNING` — race-free because
  only the host mutates status, so no atomic-claim SQL is needed.
- [x] JSON args/results contract, **enforced at the enqueue boundary**
  (`_assert_json_safe`) and on task return (`assert_json_result` → a non-JSON return
  is a clean task error, not a host crash).
- [x] Requeue tasks left `running` by a host crash on startup (`requeue_running`;
  at-least-once). **Idempotency caveat — see Section 6 #1.**

### B. Task definition & registration
- [x] `@task()` decorator: name→fn registry; calling the fn **enqueues** and returns a
  lightweight `Result`.
- [x] `task.s(*args, **kwargs)` signature object for composing chords/pipelines.
- [x] `task.schedule(args=, delay=)` → INSERT with `eta = now + delay`.
- [x] `context=True`: passes a `TaskContext` exposing `.id` (the queue row id) —
  replaced `JobResult.huey_task_id` (renamed to `task_id`).
- [x] Registry bootstrap module the children import on spawn.

### C. Composition
- [x] **Group + chord**: N members share a `group_id`; the last to finish enqueues the
  callback once with the members' results. **Empty group = immediately done** (changed
  from Huey, which never fired the callback): the app no longer special-cases empty
  import/index stages (`finalize_job_task` removed; one uniform chord path).
- [x] **Pipeline / `.then()`**: on a step finishing, the next step is enqueued with
  `prev_result` appended (via `continuation_json` on the task/group row), matching
  `start_index_stage(index_job_id, prev_result=None)`.
- [x] **chord-then-pipeline**: `chord(members, cb).then(...)` reproduced; `index_stage`
  uses `chord(members, complete_job_callback.s(index_job_id))`.
- [x] `enqueue(pipeline)` persists a composed graph.
- [x] Result passing: positional append; chord callback receives the member-results
  list (signature preserved; values still ignored downstream).

### D. Scheduling
- [x] `@periodic_task(cron)` + a per-minute scheduler tick. Only `dispatch_scheduled_tasks`
  exists; a `CronSpec` (`cron.py`) evaluates minute granularity.
- [x] Delayed/ETA dispatch: `eta<=now` rows become claimable.
- [x] Single-fire for periodics via `periodic_state` (claim a `(name, minute)` once, so
  a slow tick can't double-enqueue).

### E. Concurrency & process management
- [x] `spawn` worker pool of N children (CLI `-w/--workers`, default CPU count).
- [x] Assignment IPC (host→child inbox) + result/exception IPC (child→host outbox).
- [x] **Crash isolation**: dead child (incl. SIGSEGV exit 139) detected by exit code;
  in-flight task marked errored + composition advanced; child **respawned**. (Covered by
  `tests/yaffo/taskq/test_host_spawn.py`.)
- [x] Worker recycling after K tasks (CLI `-r/--recycle`, caps native memory).
- [x] Exception capture: child exceptions serialized back as `ERROR`; host records on the
  task row and never dies.

### F. Locks
- [x] `@lock_task(name)` named locks: a held lock makes other same-lock tasks **skip**
  (no-op), via `task_lock` + `try_acquire_lock`/`release_lock` (`file_sync` uses it).

### G. Producer / app integration
- [x] Drop-in `enqueue` — all existing call sites kept working with minimal edits.
- [x] Cooperative cancellation unchanged: tasks poll `get_job_status(job_id)`; no
  queue-level revoke.
- [x] `Job` / `JobResult` writes unchanged.

### H. Test & dev ergonomics
- [x] **Immediate mode** (`task_queue.immediate = True`): runs pipelines/chords
  synchronously in-process; `tests/yaffo/utils/test_index_jobs.py` drives the real
  pipeline through it.
- [x] `inv start-tasks` launches the host; `inv app-local` runs Flask + host + watcher.
- [x] Structured logging to `background_tasks.log` (host + per-child) + graceful
  shutdown on SIGINT/SIGTERM (drain inbox sentinel, join, then terminate).
- [x] Startup logs worker count + recycle interval + periodic-task names.
  - ~~Registered-task listing on startup (consumer parity)~~ — **dropped, by design.**
    The host **never imports the task registry** (so it can't load the native ML stack);
    the *children*
    hold it. The host therefore has no task list to print. Incompatible with the
    "host does no task work" rule, so deliberately not implemented.

### I. Cutover
- [x] Migrated module-by-module; full import→index chord pipeline verified end-to-end
  with N spawn workers, no segfaults.
- [x] `huey` removed from `pyproject.toml`; Huey `config.py`/`main.py` bits deleted;
  `job_results.huey_task_id` → `task_id`; `docs/automations.md` updated.
  - ⚠️ **`docs/deployment/gcp-demo-architecture.md` still referenced "huey" / `yaffo-huey.db`**
    — missed in the original cutover, corrected 2026-06-21.

## 6. Risks & open questions — outcomes

- **At-least-once vs at-most-once.** ⚠️ **Default is at-least-once and confirmed,
  but the "confirm tasks tolerate replay" step was deferred past the migration and
  later found a real bug.** A 2026-06-21 idempotency audit of every task found
  `index_photo_task` was *not* replay-safe — a requeued task duplicated a photo's
  faces (face thumbnail paths carry a uuid, so the unique constraint couldn't catch
  the re-insert) and leaked thumbnails. **Fixed** (delete-then-insert via
  `clear_faces_for_photos`). `import_photo` (unique `full_file_path`),
  `find_duplicates` (unique `JobResult.task_id`), and `remove_duplicates`
  (`exists()` guards) are replay-safe; `duplicate_scan` has a rare, non-corrupting
  redundant-scan window left as-is. **Policy:** tasks must be idempotent; rely on a
  unique constraint or delete-then-insert. Lesson: this should have been verified at
  migration time, not assumed.
- **SQLite write contention.** ✅ Mitigated as planned — WAL + `busy_timeout` +
  host-as-sole-status-mutator; producers only INSERT. No contention issues observed
  at our scale.
- **Composition complexity.** ✅ Resolved — covered by `test_coordinator.py`
  (persistent) and `test_index_jobs.py` (immediate-mode spec), including the
  empty-group special case.
- **Windows.** ❌ **Unverified.** `spawn` is cross-platform, but child IPC, signal
  handling, and SQLite file locking were never actually exercised on Windows — all
  dev and packaging (PyInstaller `.app`/DMG) has been macOS. Concretely, `host.py`
  installs `SIGTERM`/`SIGINT` handlers (`SIGTERM` is effectively unsupported on
  Windows) and uses `proc.terminate()`. Still a stated target; treat Windows
  background-tasks as **untested** until validated.
- **Args serialization.** ✅ Enforced at the `enqueue` boundary (`_assert_json_safe`)
  and on return (`assert_json_result`).
- **Scheduler scope.** ✅ As planned — minute-granular periodic + delayed only; no
  full crontab engine (automation cron expressions are evaluated at the app layer).

## 7. References

- Findings on the Huey crash (this repo's investigation): thread-dlib SIGSEGV;
  process-worker spawn/fork failures; macOS `os_log` fork-safety.
- Huey CHANGELOG — process-worker start-method regression/fix.
- Existing spawn-based precedent: `yaffo/utils/index_photos.py:index_photos_batch`
  (`ProcessPoolExecutor`), the model for the child pool.
