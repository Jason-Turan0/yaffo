# Automations — Scheduled & Event-Driven Background Behavior (Architecture)

> **Status (2026-06-14):** runtime + builder + UI built and unit-tested. An
> **Automation** is a named unit of functionality that runs on a **schedule**
> (cron) or in response to a **domain event** (e.g. photos indexed). Two tiers,
> mirroring the theme registry: **system** automations ship with the app and are
> code-backed; **custom** automations are AI-generated and run sandboxed Starlark.
> The AI builder chat + publish flow and the management UI are live under the
> **Utilities** page (`/utilities/automations`). This doc is the reference for the
> data model, the two dispatch paths, the sandbox, the builder, and the design
> decisions behind them.
>
> **Not yet built:** the loop guard; a hard Starlark CPU/time limit. See
> *Deferred* at the bottom.

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
| `published_code` | **custom**: the **live** Starlark the dispatchers run; **system**: `NULL` |
| `working_code` | **custom**: the in-progress draft the builder edits before publishing; **system**: `NULL` |
| `status` | generation lifecycle for custom (`IN_PROGRESS/READY/FAILED/ACCEPTED`); system rows are `READY` |

`published_code` vs `working_code` is a page-style published/working split: the
executor only ever runs `published_code`, so a draft can't fire until it's
published (copy `working_code → published_code`, status `ACCEPTED`).

Relationships: `triggers` (cascade), `jobs` (run history — see below), `messages`
(the builder chat — `Conversation` rows via `conversations.automation_id`, which is
nullable and shared with the page builder's `version_id`).

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
- **custom** — `handler is None` and `published_code` set → enqueue
  `run_automation_code_task` with the automation id + serialised context.
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
  `automation.published_code` with `inputs={"ctx": …}` (the trigger context:
  `event_type`/`job_id`/`photo_ids`, empty for a schedule) and
  `functions=build_host_functions(session)`. Returns the `StarlarkResult`.
- **`tasks/run_automation.py::run_automation_code_task`** — the registered huey
  task wrapping the executor (loads the automation, rebuilds the `EventContext`,
  records the run as a Job via `run_and_record`).

The sandbox parses authored code with the **extended Starlark dialect**, so
scripts may use top-level `for`/`if`/f-strings/lambda while staying hermetic
(`load` is inert with no file loader; still no `while`/recursion/I/O).

## The builder (AI chat + publish + UI)

Custom automations are authored by an AI chat that writes their Starlark, mirroring
the theme builder: async via huey, durable on the automation, browser observes by
polling. Reuses the page-builder agent/model infrastructure.

- **Persistence** (`db/repositories/automation_repository.py`): `add_message`
  (Conversation rows via `automation_id`), `set_status`, `write_working_code`,
  **`publish`** (working → published, `ACCEPTED`), `discard_draft`, `get_status`.
- **Tool** (`page_builder/tool_providers/automation_tool.py`) —
  `write_automation_code`: parse-checks via `validate_starlark` and persists into
  `working_code`, returning syntax errors to the model to retry.
- **Prompts** — `prompt_generator/automation_system_prompt.py` (stable: language
  rules, the `ctx` contract, the host API via `render_host_api()`, data sources via
  `FIELDS_BY_SOURCE`, the `EVENTS` catalog — all *derived*, none restated) +
  `automation_user_prompt.py` (volatile: request + current code).
- **Agent + task** — `agent.create_automation_builder_agent` (data-query +
  write-code tools); `tasks/generate_automation.py::generate_automation_task` runs
  it, persisting the conversation and driving `IN_PROGRESS → READY/FAILED`, with
  cooperative cancel via `get_automation_status`.

### UI (`/utilities/automations`)
A first-class **utility** (not a top-level page). The Automations panel is the
*second* stacked nav in the shared utilities sidebar
(`templates/utilities/_base.html`), populated on every utilities page by
`automations_sidebar_context()` (each utilities route spreads it — deliberately not
a context processor). The detail (triggers, published code, chat, publish/discard,
enable/delete) renders in `utility_content` (`templates/utilities/automations.html`).
Routes: `routes/utilities/automations.py` at `/utilities/automations/...` (endpoint
names kept as `automations_*`). The "New automation" modal lives in `_base.html`
and is wired by `static/utilities/_base.js` (deferred to `DOMContentLoaded` since
`modal.js` loads later in the body), so New works from any utilities page.

**Trigger editing** is the HTMX server-rendered-fragment pattern (the one in
`CLAUDE.md`): the `templates/utilities/automations_triggers.html` fragment is
`{% include %}`d on first page render and re-rendered in place by the single
`automations_triggers` endpoint (`POST .../<slug>/triggers`), which dispatches on
an `action` from `hx-vals` (`save_schedule` / `add_event` / `remove` / `toggle`).
`save_schedule` adds or edits in place — an `edit_trigger_id` form field (empty =
add) tells the route which; a saved schedule leaves `next_run_at` NULL so the
dispatcher (re)initialises it from the cron on the next tick (its "freshly enabled"
path). Trigger edits are allowed on **system** automations too — a schedule is
runtime state (the dispatcher reads rows live), so users can reschedule built-ins
like `file_sync`, even though the automation's code/identity stays route-locked.

**Capturing a cron** is the one place that breaks from server-rendered HTMX: the
cron editor is a client-side component (`static/components/cron_builder.{js,css}`,
mounted by `<div data-cron-builder>`) because a per-keystroke/per-dropdown server
round-trip is the wrong fit for an interactive widget (cf. react-js-cron). It
offers a preset list + a Period-driven single-value builder (Hourly/Daily/Weekly/
Monthly) + an Advanced raw-cron escape hatch, composes one 5-field cron into a
hidden `cron` input, and live-previews it via `describeCron` (which also fills the
`data-cron` text on existing rows). The server stays the trust boundary: the
`add_schedule` action only validates the submitted `cron` with `is_valid_cron`
before persisting — no cron-building logic lives in Python. The component re-inits
itself on load and on `htmx:afterSwap`, so it survives the fragment re-render.

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
  case). Note custom-run Jobs are finalised synchronously and never go through
  `complete_job_task`, so they don't emit events today — a partial mitigation.
- **Hard CPU/time limit.** Starlark blocks unbounded loops/recursion, but a large
  bounded `for` can still burn CPU; `starlark-pyo3` exposes no step budget and a
  thread soft-timeout can't kill a runaway eval. Real hardening = subprocess +
  kill / resource limits before exposing arbitrary user scripts.
- **Job Status** Show status of the automation on the UI
- **Testing features** Ability to run the script generated by the AI from the UI and get a preview (no actions taken) of what the script would do.

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
| Builder persistence (publish/chat) | `yaffo/db/repositories/automation_repository.py` |
| Builder tool | `yaffo/page_builder/tool_providers/automation_tool.py` |
| Builder prompts | `yaffo/page_builder/prompt_generator/automation_{system,user}_prompt.py` |
| Builder agent + task | `yaffo/page_builder/agent.py`, `yaffo/background_tasks/tasks/generate_automation.py` |
| UI routes | `yaffo/routes/utilities/automations.py` (+ `common.automations_sidebar_context`) |
| UI templates / static | `yaffo/templates/utilities/{_base,automations,automations_triggers}.html`, `yaffo/static/utilities/{_base,automations}.{js,css}` |
| Cron editor component | `yaffo/static/components/cron_builder.{js,css}` |
| Tests | `tests/yaffo/background_tasks/`, `tests/yaffo/routes/test_automations_page.py` |
