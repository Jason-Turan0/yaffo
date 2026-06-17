# Automations — Scheduled & Event-Driven Background Behavior (Architecture)

> **Status (2026-06-16):** runtime + builder + management UI + host action API +
> a test/preview harness, all built and unit-tested. An **Automation** is a named
> unit of functionality that runs on a **schedule** (cron) or in response to a
> **domain event** (e.g. photos indexed). Two tiers, mirroring the theme registry:
> **system** automations ship with the app and are code-backed; **custom**
> automations are AI-generated and run sandboxed Starlark against a curated host
> API (read `data_query` + face-similarity; mutating tag / rename / move /
> assign-face). The AI builder chat + publish flow, full trigger-editing UI (cron
> builder), name/description editing, and a no-op **Test** harness (preview the
> actions a script would take) are live under the **Utilities** page
> (`/utilities/automations`). This doc is the reference for the data model, the two
> dispatch paths, the sandbox + host API, the builder, the test harness, and the
> design decisions behind them.
>
> **Not yet built:** the loop guard; a hard Starlark CPU/time limit; per-automation
> run status on the UI. See *Deferred* at the bottom.

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
| `config` | **system**: JSON of runtime-tunable settings (e.g. `{"threshold": 0.95}`); **custom**: `NULL`. Schema declared in `background_tasks/automation_config.py`, edited via the Configure modal |
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

- **Schedule-driven system** automations record via their concrete tasks (e.g.
  file_sync's import/index Jobs are tagged with `automation_id`).
- **Event-driven system** automations (`auto_assign_faces`, `export_photo_tag`,
  `assign_location_name`) record via `background_tasks/automation_runs.py`
  `record_run`: a RUNNING Job (named by slug) is opened, the handler's work runs and
  returns a one-line summary, then the Job is finalised to COMPLETED (summary in
  `job_data.output`) or FAILED (the work's exception captured, not re-raised). So an
  event-triggered run now shows up in the detail page's run history like a scheduled
  one.
- **Custom** automations record via the same module's `run_and_record`: a RUNNING
  Job is opened, the sandboxed code runs, then the Job is finalised to
  COMPLETED/FAILED with the captured print `output` in `job_data` (and `error` on
  failure). The sandbox returns failures as data, so a bad script becomes a FAILED
  Job, not an exception.

Both `record_run` and `run_and_record` share `_open_run_job` and, like the custom
path, **never hand their Jobs to `complete_job_task`** — so an automation run emits
no job-completion event (and can't feed a trigger loop). Domain events an automation
*chooses* to emit (e.g. `assign_location_name` emitting `photo_modified` so
`export_photo_tag` writes the file) are explicit `emit_event` calls inside the work,
independent of the run's Job.

Schema lives in `yaffo/scripts/init_db.py` (no migrations — edit + reseed; the
`file_sync` system automation + its hourly schedule trigger, the
`auto_assign_faces` automation + its `photo_indexed` event trigger, the
`duplicate_scan` automation + its daily schedule trigger, and the
`export_photo_tag` automation + its `photo_modified` event trigger, are seeded
there).

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
`duplicates_found`, `photo_modified` (`EVENTS`).

**Not all events come from job completion.** `photo_modified` is emitted
*synchronously from routes* (not via `JOB_EVENT_MAP`) when a user edits a photo's
people/location: face assign (`routes/faces.py`), person rename + face removal
(`routes/people.py`), and location bulk-update (`routes/locations.py`) each call
`emit_event(EVENT_PHOTO_MODIFIED, {"photo_ids": [...]})` after their commit. These
are UI edits, not bulk jobs, so per-edit granularity is fine (no fan-out storm).

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
  (`HostFunction` specs) and read in three places that can't diverge:
  `build_host_functions(session)` (the live, session-bound callables),
  `build_recording_host_functions(session)` (the test/preview variant — see below),
  and `render_host_api()` (agent-facing docs for the system prompt). Add a
  capability = add one `HostFunction`. Each spec carries `impl` (takes the session
  first; delegates DB work to `db/repositories`), `mutating` (state-changing →
  recorded-but-skipped in a test), and `summarize(args, session)` (a friendly
  one-line action for the test UI, resolving ids to file/person names via
  `automation_sandbox/labels.py`). Current surface:

  | function | kind | does |
  |---|---|---|
  | `data_query(query)` | read | `data_query_repository.resolve_query`; photo rows are enriched with `media_dir_id` + `relative_path` (see *Media dirs*) |
  | `face_similarity(photo_id, person_id)` | read | per-face similarity to a person (`domain/compare_utils`) |
  | `match_people(photo_id)` | read | per-face similarity to all known people |
  | `tag_photo(photo_id, name, value=None)` | mutating | add a `Tag` |
  | `rename_file(photo_id, new_name)` | mutating | rename in place (basename only) |
  | `move_photo(photo_id, media_dir_id, target_path)` | mutating | move into a sub-folder of a media dir (confined to it) |
  | `assign_face(face_id, person_id)` | mutating | link a face to a person (per-face, not per-photo) |

  The actions live in `automation_actions.py`; the read comparisons in
  `automation_compare.py`; both keep all `session.query/add/commit` in
  `db/repositories`. **Host functions return string-keyed dicts / lists of them**:
  the `starlark-pyo3` binding coerces returned dict *keys* to strings, so ids are
  values, not keys (e.g. `match_people` → `[{face_id, matches:[{person_id,
  person_name, score}]}]`).
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

## Test / preview harness (`automation_sandbox/preview.py`)

A **dry run** that shows what a script *would* do without doing it. `Test`
re-runs the last-selected file/folder; `Test on a folder…` / `Test on a file…`
open the native picker (`select_folder?mode=folder|file`, see `utils/file_system.py`)
and run against the indexed photos at/under that path
(`photos_repository.get_photo_ids_under_path`). The route is `POST
.../<slug>/test-files` → `preview_automation(session, automation, photo_ids)`.

- **Nothing changes.** `preview_automation` runs the code with
  `build_recording_host_functions`: every host call is appended to a `HostCall`
  list; **reads still execute** (so the script sees live data), **mutating actions
  are recorded but not performed**. No `Job` is opened.
- **The result is a named DTO** `TestRunResult` (success, `code_source`
  working/published, `context`, `actions`, `output`, `value`, `error`). Each action
  carries a friendly `summary` (via `summarize_call`) plus the raw `name`/`args`.
- **UI** (`static/utilities/automations.js` + the code panel in
  `automations.html`): renders a meta line (`Testing on folder: … / Ran published
  code · N photos`), then an **Actions table** — consecutive runs of the same
  action collapse to one row with a `× N` count, and `Show details` reveals the
  raw calls. All built with safe DOM construction (no innerHTML injection).

## Media dirs (script-facing paths)

Scripts never see absolute paths. Each configured media dir has a stable **uuid4**
guid, stored in the `media_dirs` setting as `[{id, path}]` (helpers in
`utils/settings.py`: `get_media_dir_entries`, `media_dir_by_id`, `add_media_dir`,
`remove_media_dir`; the add-media-dir route assigns the guid; legacy string entries
are migrated by `scripts/backfill_media_dir_ids.py`). The automation `data_query`
wrapper enriches photo rows with `media_dir_id` + `relative_path`
(`automation_sandbox/media_dirs.py::enrich_photo_rows`, derived from
`full_file_path`, which stays server-side); the shared `resolve_query` (page
builder) is untouched. `move_photo` addresses its destination by `media_dir_id` +
`target_path` and **refuses any target that resolves outside that media dir**, so a
script can organise within or move between media dirs but can't write elsewhere.

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
`save_schedule` action only validates the submitted `cron` with `is_valid_cron`
before persisting (`automations_validate_cron` also gates the Save button live for
the Advanced field) — no cron-building logic lives in Python. The component
re-inits itself on load and on `htmx:afterSwap`, so it survives the fragment
re-render.

**Trigger editing lives on its own screen** (`GET .../<slug>/triggers/edit`,
`automations_triggers_edit.html`) to keep the detail page uncluttered — the detail
header links to it via **Edit triggers**. **Name + description** are editable for
custom automations through an **Edit details** modal (`render_modal`, `POST
.../<slug>/details`); the slug stays fixed on rename. The detail page also hosts
the **Test** panel (above) and the published `code`.

## Built-ins

### `file_sync`

`tasks/file_sync.py` — a system automation (`handler='file_sync'`, seeded
disabled, hourly). Its handler `enqueue_file_sync` enqueues `file_sync_task`
(wrapped in `@huey.lock_task('file-sync')` so slow scans can't overlap), which
runs `utils/file_sync.run_file_sync`: the same disk↔index reconcile as the manual
index-photos button (`scan_media_dirs` + `perform_sync` are shared with the
route), so its import/index Jobs show up in the UI exactly like a hand-triggered
sync — tagged with `automation_id` as the run history.

### `auto_assign_faces`

`tasks/auto_assign_faces_automation.py` — a system automation
(`handler='auto_assign_faces'`, seeded disabled with a `photo_indexed` **event**
trigger). On each indexed batch its handler `enqueue_auto_assign_faces` enqueues
`auto_assign_faces_automation_task(automation_id, photo_ids)`, which for every
detected face computes `calculate_face_similarity` against all known people and
links the face to the **one** person clearing the configured threshold — a face
with zero or several strong matches is left unassigned. The threshold is the lone
**configurable** setting: stored in `config["threshold"]`, declared in
`automation_config.AUTOMATION_CONFIG`, edited via the **Configure** modal on the
detail page. (Was a Starlark seed example before it was promoted. It also replaced
the old manual "Auto-Assign People" utility page — that page, its route, and its
`auto_assign_faces` batch task were removed in favour of this automation.)

### `duplicate_scan`

`tasks/duplicate_scan.py` — a system automation (`handler='duplicate_scan'`, seeded
disabled with a **daily** `0 3 * * *` schedule trigger). Its handler
`enqueue_duplicate_scan` enqueues `duplicate_scan_task`, which opens a
`find_duplicates` Job over **every indexed photo** (`photos_repository.get_all_photo_paths`),
tags it with `automation_id`, and hands it to the existing `find_duplicates_task`
— the exact perceptual-hash scan the manual **Remove Duplicates** tool runs, so its
results show up there identically. Same shape as `file_sync`: a lightweight handler
→ task → reuse of an existing job. (`_open_scan_job` is the testable core; a schedule
run passes `context=None`, which the handler ignores — a full-library scan has no
event subjects.)

### `export_photo_tag`

`tasks/export_photo_tag.py` — a system automation (`handler='export_photo_tag'`,
seeded disabled with a `photo_modified` **event** trigger). When a photo's people
or location change in the UI, its handler `enqueue_export_photo_tag` enqueues
`export_photo_tag_task(automation_id, photo_ids)`, which writes the photo's tags
back into the **file's** metadata via `utils.write_metadata.write_photo_metadata`
(the same writer the old manual "Sync Metadata" utility used). Two independent
**config** toggles decide what's written: `export_location_tag_enabled` (the
photo's `location_name`) and `export_people_tag_enabled` (names of people linked to
the photo's faces, deduped + sorted); with neither enabled the run is a no-op, and
photos whose file is missing are skipped. `_export_tags` is the testable core. This
is the event-driven replacement for the deleted Sync Metadata page — instead of a
batch button, the on-disk file stays in sync as you tag.

**Known gaps (pick up later):**
- **Backfill is manual, scoped by Run-now.** Events only name the photos they
  concern, so existing photos aren't touched until you re-edit them — but **Run on a
  folder…/file…** (see *Run-now* below) now re-runs the handler for real over a
  picked path's photos, so you can apply it to existing files without re-editing.
- **Format is dispatched by file *extension*** (`write_metadata.py`), so a WebP
  file mislabeled `.jpg` takes the JPEG path. exiftool usually copes, but
  detecting the real format (magic bytes / exiftool) would be more robust.
- **Verify with** `inv tags <path>` (or `python -m yaffo.scripts.print_photo_tags
  [--all] <path>`) — prints the `XMP:PersonInImage` / `XMP:Location` the handler
  writes, via the bundled exiftool.

### `assign_location_name`

`tasks/assign_location_name_automation.py` — a system automation
(`handler='assign_location_name'`, seeded disabled with a `photo_indexed` **event**
trigger). On each indexed batch its handler `enqueue_assign_location_name` enqueues
`assign_location_name_automation_task(automation_id, photo_ids)`, which gives each
GPS-tagged photo a `location_name` via two strategies tried cheapest-first per
photo: **(1) reuse** the name of the closest already-named photo within
`nearby_radius_meters` (`photos_repository.get_named_coordinates` +
`utils/geo.haversine_meters`), then **(2) reverse-geocode** the coordinates via
`utils/reverse_geocode.reverse_geocode` (the same Nominatim helper the Locations
screen uses — extracted from `routes/locations.py` so both share one
implementation), throttled to ~1 req/sec. A photo named in step 2 joins the reuse
candidates for the rest of the batch, so a cluster of fresh photos costs one online
lookup, not one each. Four **config** fields tune it: `reuse_nearby_enabled`,
`nearby_radius_meters` (default 1000), `reverse_geocode_enabled`, and
`overwrite_existing` (off → photos that already have a name are left alone).
Photos without GPS are skipped. After committing, it **emits `photo_modified`** for
the named photos so `export_photo_tag` can write the new location into the file —
a deliberate `photo_indexed → assign_location_name → photo_modified →
export_photo_tag` chain (not a loop: export_photo_tag emits nothing).
`_assign_location_names` is the testable core (the geocoder is injected).

### `geotag_from_neighbors`

`tasks/geotag_from_neighbors_automation.py` — a system automation
(`handler='geotag_from_neighbors'`, seeded disabled with a `photo_indexed` **event**
trigger). On each indexed batch its handler enqueues
`geotag_from_neighbors_automation_task(automation_id, photo_ids)`, which gives each
GPS-less photo (`photos_repository.get_photos_missing_gps`) the coordinates of the
closest-in-time photo that *does* have GPS — time-correlation geotagging, for the
case of a no-GPS camera shooting alongside a phone on the same outing. Candidates
(`get_gps_timestamps` — every photo with a date + coordinates) are parsed to
datetimes, sorted once, and matched per target with a `bisect` over the sorted times;
the nearest within the configurable window wins. When the matched source already has
a `location_name`, that's copied to the target too (unless the target already has its
own). The lone **config** field `max_minutes` (default 30) bounds the window so
coordinates aren't copied across a long gap. Candidates are frozen at the start of the
run, so a just-geotagged photo is never reused as a source (inferred coordinates can't
chain/drift).
`_geotag_from_neighbors` is the testable core. It writes lat/lon to the **index**
only (it does not emit `photo_modified`); pairs naturally with `assign_location_name`
on a later run. **Depends on consistent `date_taken` clocks across photos** — see the
timezone note below.

### Configurable system automations

A system automation can expose runtime-tunable settings without a code change.
`background_tasks/automation_config.py` is the single source of truth: a list of
`ConfigField`s keyed by handler, read in two non-diverging places — the route
(`automations_update_config` validates each field against its `[min, max]` and
writes `Automation.config`; bounds are the trust boundary) and the running task
(reads the live value via `config_value`). The detail page renders a **Configure**
modal (`render_modal`) when the selected automation declares fields. Config is
runtime state the task reads live (like a schedule), so it's editable on system
rows even though their code/identity stays route-locked.

## Seed examples (`scripts/seed_automations.py`)

A dev seeder (run `python -m yaffo.scripts.seed_automations`, idempotent) that
stands in for AI-generated custom automations so the runtime + host API can be
exercised end-to-end without the builder: `log-photos-on-index` /
`log-photos-each-minute` (read-only `data_query`) and `organize-by-date` (on
`photo_indexed`, `move_photo(id, media_dir_id, "YYYY/MM")` from each row's
`media_dir_id`). (Auto-assign-faces used to be a seed example here; it was promoted
to the `auto_assign_faces` system built-in above. The seeder still deletes the old
`auto-assign-faces` slug so re-running it cleans up any stale custom copy.)

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
  case). Note automation-run Jobs (both `record_run` and `run_and_record`) are
  finalised synchronously and never go through `complete_job_task`, so the run Job
  itself emits no event — a partial mitigation. An automation that *explicitly*
  emits (e.g. `assign_location_name` → `photo_modified` → `export_photo_tag`) is the
  case a real loop guard must still bound.
- **Hard CPU/time limit.** Starlark blocks unbounded loops/recursion, but a large
  bounded `for` can still burn CPU; `starlark-pyo3` exposes no step budget and a
  thread soft-timeout can't kill a runaway eval. Real hardening = subprocess +
  kill / resource limits before exposing arbitrary user scripts. **More pressing
  now** that mutating actions (move/rename/tag/assign) run real file/DB writes on a
  triggered run.
- **`media_dir_id` / `relative_path` aren't filterable.** They're enrichment, not
  `FIELDS_BY_SOURCE` columns, so a script can read them on a photo row but can't
  `data_query` *by* them (e.g. "photos in media dir X"). Would need a real filter
  mechanism.
- **Built (was deferred):** trigger-editing UI + cron builder; the test/preview
  harness (mutating actions are recorded-not-performed); **Run now**
  (`automations_run_now`, independent of triggers/enabled) — a whole-library handler
  (file_sync/duplicate_scan) fires context-less like a schedule tick, while every
  other automation gets **Run on a folder…/file…** buttons that pick a path and
  invoke for real over the indexed photos under it (`get_photo_ids_under_path` →
  `EventContext(event_type="manual", photo_ids=…)` → `invoke_automation`), the live
  twin of the test-files dry run; whether an automation is scoped is
  `AUTOMATION_WHOLE_LIBRARY_HANDLERS` (route `_supports_scoped_run`). And **run history on the
  detail page** — `automation_repository.get_recent_jobs` + the `AutomationRunView`
  view-model render the recent Jobs (system *and* custom runs) as a status/percent/
  summary/time list. The `automations_runs.html` fragment self-polls every 5s
  (`automations_runs` endpoint), so in-progress runs appear and tick toward
  completion (with a live percent) without a reload.
- **More complex use cases** More use cases of the automation feature to stress test the API.
- **~~GPS Parsing from file is probably incorrect~~ (fixed)** The "located to China"
  symptom was a longitude sign bug — exiftool's `-n` reports `EXIF:GPSLongitude` as
  an unsigned magnitude (hemisphere in the `…Ref` tag), so `90° W` was stored as
  `+90` (→ China). Fixed in `utils/index_photos.get_signed_gps_from_exiftool`
  (prefers the signed `Composite:GPS*`, else applies the Ref); existing rows were
  corrected by re-reading the files.
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
| Sandbox runner + executor | `yaffo/background_tasks/automation_sandbox/{starlark_runner,executor}.py` |
| Host API (registry + docs) | `yaffo/background_tasks/automation_sandbox/automation_host.py` |
| Host actions / comparisons / labels | `yaffo/background_tasks/automation_sandbox/{automation_actions,automation_compare,labels}.py` |
| Media-dir guids + row enrichment | `yaffo/utils/settings.py`, `yaffo/background_tasks/automation_sandbox/media_dirs.py`, `yaffo/scripts/backfill_media_dir_ids.py` |
| Test / preview harness | `yaffo/background_tasks/automation_sandbox/preview.py` (+ `routes` `test-files`, `utils/file_system.py` picker) |
| Executor task | `yaffo/background_tasks/tasks/run_automation.py` |
| Run → Job recording (system `record_run` + custom `run_and_record`) | `yaffo/background_tasks/automation_runs.py` |
| Built-in file_sync | `yaffo/background_tasks/tasks/file_sync.py`, `yaffo/utils/file_sync.py` |
| Built-in auto_assign_faces | `yaffo/background_tasks/tasks/auto_assign_faces_automation.py` |
| Built-in duplicate_scan | `yaffo/background_tasks/tasks/duplicate_scan.py` |
| Built-in export_photo_tag | `yaffo/background_tasks/tasks/export_photo_tag.py` (+ emit hooks in `routes/{faces,people,locations}.py`) |
| Built-in assign_location_name | `yaffo/background_tasks/tasks/assign_location_name_automation.py` (+ `utils/reverse_geocode.py`, `utils/geo.py`, `photos_repository.{get_photos_with_coords,get_named_coordinates}`) |
| Built-in geotag_from_neighbors | `yaffo/background_tasks/tasks/geotag_from_neighbors_automation.py` (+ `photos_repository.{get_photos_missing_gps,get_gps_timestamps}`) |
| Tag inspector (debug) | `yaffo/scripts/print_photo_tags.py` (`inv tags <path>`) |
| System-automation config schema | `yaffo/background_tasks/automation_config.py` |
| Seed examples | `yaffo/scripts/seed_automations.py` |
| Builder persistence (publish/chat) | `yaffo/db/repositories/automation_repository.py` |
| Builder tool | `yaffo/page_builder/tool_providers/automation_tool.py` |
| Builder prompts | `yaffo/page_builder/prompt_generator/automation_{system,user}_prompt.py` |
| Builder agent + task | `yaffo/page_builder/agent.py`, `yaffo/background_tasks/tasks/generate_automation.py` |
| UI routes | `yaffo/routes/utilities/automations.py` (+ `common.automations_sidebar_context`) |
| UI templates / static | `yaffo/templates/utilities/{_base,automations,automations_triggers,automations_triggers_edit}.html`, `yaffo/static/utilities/{_base,automations}.{js,css}` |
| Cron editor component | `yaffo/static/components/cron_builder.{js,css}` |
| Tests | `tests/yaffo/background_tasks/` (incl. `test_automation_{host,actions}`, `test_preview`), `tests/yaffo/routes/test_automations_page.py` |
