# Automations — Scheduled & Event-Driven Background Behavior (Architecture)

> **Status:** built and unit-tested; not yet exposed in the UI. An **Automation**
> is a named unit of functionality that runs on a **schedule** (cron) or in
> response to a **domain event** (e.g. photos indexed). Two tiers, mirroring the
> theme registry: **system** automations ship with the app and are code-backed;
> **custom** automations are AI-generated and run sandboxed Starlark. This doc is
> the reference for the data model, the two dispatch paths, the sandbox, and the
> design decisions behind them.

## Overview

```
                    ┌──────────────────────── triggers ────────────────────────┐
                    │                                                           │
   huey scheduler   │   schedule trigger (cron + next_run_at)                   │
   every 60s  ──────┼─►  dispatch_scheduled_tasks ──┐                           │
                    │                                │                          │
   a job completes  │   event trigger (event_type)   ├─► invoke_automation ─────┤
   emit_event  ─────┼─►  dispatch_event_task ────────┘        │                 │
                    │                                          │                 │
                    └──────────────────────────────────────────┼─────────────────┘
                                                               │
                            ┌──────────────────────────────────┴───────────┐
                            │                                               │
                     system tier                                    custom tier
                  (handler in HANDLERS)                       (handler is None, code set)
                            │                                               │
                  enqueue the system task                     run_automation_code_task
                  (e.g. file_sync_task)                                     │
                            │                                    run_starlark(code,
                            │                                      inputs=ctx,
                            ▼                                      functions=host API)
                     Jobs (run history) ◄───────────────────────────────────┘
                     jobs.automation_id
```

The trigger model is fixed at import time only in that **action *types*** (system
handlers, the event catalog) are code; the **schedule/subscription *rows*** are
fully runtime-editable with no consumer restart.

## Data model (`yaffo/db/models.py`)

### `Automation` — the definition
| column | meaning |
|---|---|
| `slug` | stable unique id (`file_sync`, or slugified for custom) |
| `name`, `description` | display |
| `is_system` | ships with the app; route-locked against edit/delete (themes pattern) |
| `enabled` | master switch |
| `handler` | **system**: key into `background_tasks.registry.HANDLERS`; **custom**: `NULL` |
| `code` | **custom**: AI-generated Starlark body; **system**: `NULL` |
| `status` | generation lifecycle for custom (`IN_PROGRESS/READY/FAILED/ACCEPTED`); system rows are `READY` |

Relationships: `triggers` (cascade), `jobs` (run history — see below).

### `AutomationTrigger` — when it runs
One automation → many triggers, so it can run on a schedule *and* react to events.
- `trigger_type` = `schedule` | `event`, plus a per-trigger `enabled`.
- **schedule**: `cron` (5-field) + the dispatcher bookkeeping `next_run_at` / `last_run_at`.
- **event**: `event_type` (from the `EVENTS` catalog) + `config` (JSON: filters / args).

### Runs reuse `Job`
There is **no `automation_runs` table**. A run is a `Job` tagged with
`jobs.automation_id` (`ON DELETE SET NULL`), so the existing job status / progress
/ UI machinery *is* the run history. `Automation.jobs` ↔ `Job.automation`.

- **System** automations record via their concrete tasks (e.g. file_sync's
  import/index Jobs are tagged with `automation_id`).
- **Custom** automations record via `background_tasks/automation_runs.py`
  `run_and_record`: a RUNNING Job (named by slug) is opened, the sandboxed code
  runs, then the Job is finalised to COMPLETED/FAILED with the captured print
  `output` in `job_data` (and `error` on failure). These Jobs are never handed to
  `complete_job_task`, so they emit no events (and can't feed a trigger loop).

Schema lives in `yaffo/scripts/init_db.py` (no migrations — edit + reseed; the
`file_sync` system automation + its hourly schedule trigger are seeded there).

## Schedule path (poll)

`tasks/dispatcher.py::dispatch_scheduled_tasks` is the **single** registered
periodic task — `@huey.periodic_task(crontab(minute='*'))`. huey's scheduler
enqueues it every 60s (see `huey/consumer.py` `Scheduler`, `periodic_task_seconds
= 60`). Each tick it loads enabled schedule triggers on enabled automations and,
per trigger:

- `next_run_at is None` (freshly enabled) → initialise to the next slot, **don't**
  fire this tick.
- `next_run_at <= now` → fire via `invoke_automation`, stamp `last_run_at`,
  advance `next_run_at = compute_next_run(cron, now)`.

Cron math is `croniter` (`background_tasks/schedule.py`: `compute_next_run`,
`is_valid_cron`).

**Why `next_run_at`, not exact cron-matching:** the dispatcher runs in a *worker*
after a queue hop, so `now` is the execution time, with latency. Keying off
`next_run_at <= now` gives **catch-up** (a due trigger still fires on the first
tick after its slot) and **no double-fire** (the slot advances once fired). This
is the cost of the dispatcher pattern vs huey-native `@periodic_task`, which is
evaluated exactly once per minute in the scheduler thread and so can match the
wall clock statelessly.

## Event path (push)

The push twin of the schedule dispatcher.

1. **Emit.** `events.emit_event(event_type, payload)` enqueues one
   `dispatch_event_task`. Emission is wired into `tasks/complete_job.py`: after a
   job is committed `COMPLETED`, `emit_job_completed_event` maps `job.name →
   event_type` (`JOB_EVENT_MAP`) and emits with the resolved `photo_ids`.
   **Granularity is per-job-completion** (one emission point, ~1 event per job) —
   chosen over per-photo to avoid fan-out storms on bulk imports.
2. **Dispatch.** `tasks/dispatch_event.py::dispatch_event_task(event_type,
   payload)` matches enabled `event` triggers by `event_type` on enabled
   automations and calls `invoke_automation` with an `EventContext`.

`EventContext` (`background_tasks/events.py`) is the typed payload handed to a
run: `event_type`, `job_id`, `photo_ids`.

Event catalog (fixed, in `models.py`): `photo_imported`, `photo_indexed`,
`duplicates_found` (`EVENTS`).

## Tier routing (`background_tasks/automation_dispatch.py`)

Both dispatchers funnel through `invoke_automation(automation, context) -> bool`:

- **system** — `automation.handler` is in `HANDLERS` → call the registered handler
  (which enqueues the concrete task, e.g. `file_sync_task`).
- **custom** — `handler is None` and `code` set → enqueue `run_automation_code_task`
  with the automation id + serialised context.
- **misconfigured** — neither → log and return `False` (so the schedule dispatcher
  won't stamp `last_run_at`).

### The handler registry (`background_tasks/registry.py`)
`HANDLERS: dict[str, (Automation, EventContext|None) -> None]`, populated by
`@register_handler(key)` at task-definition time. The registry imports **no task
code**; handlers self-register when their task module loads, and dispatchers read
`HANDLERS` at call time. This is what keeps the task ↔ dispatcher mapping free of
import-order cycles. The schedule dispatcher passes `context=None`; the event
dispatcher passes the `EventContext`.

## The sandbox (`yaffo/background_tasks/automation_sandbox/`)

Custom automation `code` is **Starlark** (Python-like, deterministic, hermetic:
no I/O, no imports, no `while`/recursion) run via the `starlark-pyo3` binding.

```
automation_sandbox/
  starlark_runner.py   generic hermetic wrapper: run_starlark(code, *, inputs, functions)
  automation_host.py   the host API exposed to scripts (the only host surface)
  executor.py          run_automation(session, automation, context)
```

- **`run_starlark(code, *, inputs, functions, filename) -> StarlarkResult`** —
  evaluates a snippet in a fresh sandbox. `inputs` are injected as module globals;
  `functions` are host callables; the result `value` is the trailing expression.
  `print(...)` is captured into `output` (not stdout). **Failures are returned as
  data** (`success=False, error=…`) — it never raises `StarlarkError`, so the
  worker treats a bad AI script as a value, not a crash.
- **`automation_host.py`** — the host API declared **once** in `HOST_API`
  (`HostFunction` specs) and read in two places that can't diverge:
  `build_host_functions(session)` (the live, session-bound callables) and
  `render_host_api()` (agent-facing docs for the system prompt). Current surface:
  `data_query(query)` → `data_query_repository.resolve_query`. Add a capability =
  add one `HostFunction`.
- **`executor.run_automation(session, automation, context)`** — runs
  `automation.code` with `inputs={"ctx": …}` (the trigger context:
  `event_type`/`job_id`/`photo_ids`, empty for a schedule) and
  `functions=build_host_functions(session)`. Returns the `StarlarkResult`.
- **`tasks/run_automation.py::run_automation_code_task`** — the registered huey
  task wrapping the executor (loads the automation, rebuilds the `EventContext`).

### Prompt / tool-call generation
The future automation system prompt composes three reusable sources, none
restated: `render_host_api()` (callables a script may invoke), `FIELDS_BY_SOURCE`
from `data_query_repository` (the sources/columns `data_query` accepts — already
used by the page-builder prompt), and the `EVENTS` catalog + trigger context (the
`ctx` a script receives).

## The one built-in: `file_sync`

`tasks/file_sync.py` — a system automation (`handler='file_sync'`, seeded
disabled, hourly). Its handler `enqueue_file_sync` enqueues `file_sync_task`
(wrapped in `@huey.lock_task('file-sync')` so slow scans can't overlap), which
runs `utils/file_sync.run_file_sync`: the same disk↔index reconcile as the manual
index-photos button (`scan_media_dirs` + `perform_sync` are shared with the
route), so its import/index Jobs show up in the UI exactly like a hand-triggered
sync — tagged with `automation_id` as the run history.

## Key design decisions / invariants

- **One model, two tiers.** System and custom automations share `automations`;
  `is_system` + `handler` vs `code` distinguishes them. System rows carry runtime
  state (enabled, schedule) the way themes can't, so they live in the DB (seeded),
  not a code-only registry.
- **Types fixed, instances dynamic.** Handler keys and the event catalog are code;
  schedule/subscription rows are created/edited/toggled at runtime with no consumer
  restart.
- **Runs are Jobs.** Reusing `Job` (via `jobs.automation_id`) avoids a parallel
  run table and gets the existing progress/UI for free.
- **Failures are data.** The sandbox never raises on bad scripts; the executor and
  dispatchers log and move on.
- **Cycle-break idiom.** Modules that must dispatch tasks but are imported *by*
  tasks (`events.emit_event`, `automation_dispatch.invoke_automation`,
  `index_jobs.enqueue_index_jobs`) import the task **in-function**, matching the
  pre-existing `schedule_job_completion`. The `IndexJobs` DTO was split into
  `utils/index_jobs_dto.py` for the same reason.

## Dependencies

`croniter` (schedule next-run math) and `starlark-pyo3` (the sandbox) — both in
`setup.py`.

## Deferred (flagged, not built)

- **Loop guard.** Now that an automation can both emit and subscribe to events, add
  self-trigger / depth protection. Deliberately *not* a blanket "don't emit from
  automation jobs" (that would mute the legitimate "react when file_sync indexes"
  case).
- **Hard CPU/time limit.** Starlark blocks unbounded loops/recursion, but a large
  bounded `for` can still burn CPU; `starlark-pyo3` exposes no step budget and a
  thread soft-timeout can't kill a runaway eval. Real hardening = subprocess +
  kill / resource limits before exposing arbitrary user scripts.
- **Utilities page UI.** System vs custom lists (themes structure) + the AI-build
  chat (reuse the page-builder `Conversation`/versioning pattern).

## File map

| Concern | File |
|---|---|
| Models, event catalog, constants | `yaffo/db/models.py` |
| Schema + seed | `yaffo/scripts/init_db.py` |
| Cron math | `yaffo/background_tasks/schedule.py` |
| Schedule dispatcher | `yaffo/background_tasks/tasks/dispatcher.py` |
| Event emit + job→event map | `yaffo/background_tasks/events.py` |
| Event dispatcher | `yaffo/background_tasks/tasks/dispatch_event.py` |
| Emission hook | `yaffo/background_tasks/tasks/complete_job.py` |
| Tier routing | `yaffo/background_tasks/automation_dispatch.py` |
| Handler registry | `yaffo/background_tasks/registry.py` |
| Sandbox | `yaffo/background_tasks/automation_sandbox/` |
| Executor task | `yaffo/background_tasks/tasks/run_automation.py` |
| Custom run → Job recording | `yaffo/background_tasks/automation_runs.py` |
| Built-in file_sync | `yaffo/background_tasks/tasks/file_sync.py`, `yaffo/utils/file_sync.py` |
| Tests | `tests/yaffo/background_tasks/` |
