# AI Page Builder — Async Generation & Page Versions (Design Notes)

> **Status:** design notes, not yet built. Captures the plan for moving widget
> generation off the request thread and onto a background job, with a
> **PageVersion** as the durable unit of work. Sibling to `docs/ai-page-builder.md`
> (the feature reference) — read that first for the data contract, the widget API,
> and the agent loop.

## Why

Generation runs are long. One observed run took **682s across 5 model calls**, a
single `create_widget` call producing **40,444 output tokens in 556s** (see
`model_logs/20260607-135902`). Today `POST /pages/<id>/chat` runs the whole agent
loop *inside the HTTP request* and streams NDJSON back. For a 9-minute run that
means:

- Every timeout layer is in play (gunicorn worker, nginx, GCP LB, Cloud Run,
  Cloudflare). The route already carries a `# TODO test streaming in GCP`.
- A Flask worker is pinned for the whole run.
- **Nothing is durable** — generated widgets are browser-only drafts, so closing
  the tab or dropping the connection throws away the entire run *and* the spend.
- No cancel; no real progress beyond a spinner.

**Key architectural insight** (from the "how is Claude Code implemented" chat):
the local Claude Code CLI runs the agent loop in a long-lived *local process*, so
there's no request to time out and state lives on disk. A web app can't do that —
the web-side equivalent of "a long-lived process that holds the session while the
client watches" is **a background worker (huey) writing to a durable store, with
the browser observing via poll/SSE**. That's this doc.

## Core idea: the PageVersion *is* the durable draft

When an AI request comes in, **fork a new `PageVersion`** that owns a *copy of the
current widgets*, mark it `IN_PROGRESS`, and run the agent in a **huey task** that
writes widgets / conversation / status into that version. The published page is
untouched until the user clicks Save. So:

- The "non-destructive, nothing changes until Save" principle from
  `ai-page-builder.md` still holds — but at the *page* level (the published
  version is unchanged), not by keeping drafts in browser memory.
- The work is **durable**: it survives tab close, reload, disconnect, and request
  timeouts, because it lives in a version row, not the request.
- This *simplifies* the current design — it removes the browser-held drafts, the
  in-request NDJSON stream, and likely `merge_widget_content` (the version already
  holds the real widgets; no client-vs-stored merge needed).

## Data model

```
CustomPage         id, title, subtitle, show_title, timestamps,
                   published_version_id  → PageVersion   (the live version, shown in presentation)
                   working_version_id    → PageVersion   (the in-flight version, or NULL; the UI-lock predicate)

PageVersion        id, page_id, status, created_at, started_at, completed_at,
                   error, parent_version_id (the version it was forked from)

Widget             …existing fields…, version_id        (was page_id)
Conversation       …existing fields…, version_id        (was page_id)
```

- **Widgets move from page-scoped to version-scoped.** A version owns a snapshot
  of the widget set. Presentation renders `published_version_id`'s widgets.
- **Conversation moves to the version** (per your note: "tied to a version so we
  can display the back-and-forth and status updates"). The transcript for one
  generation — user message, assistant turns, and interleaved status lines
  ("Creating widget…", "Done", errors) — lives on its version. **Forking copies
  the prior conversation into the new version** (resolved Q2), so follow-ups like
  "make it bigger" have context and the transcript reads continuously.
- **Every page always has one `ACCEPTED` version that owns its live widgets.**
  `create_page` mints an initial empty `ACCEPTED` version and sets
  `published_version_id`; a brand-new manual page is just a page whose committed
  version is empty. Manual edits mutate that version in place (no fork — resolved
  Q1: only AI requests fork).
- **`published_version_id` vs the `ACCEPTED` status are different concepts that
  compose.** `ACCEPTED` is the *status* of any saved snapshot; `published_version_id`
  points at the one accepted version that is currently **live**. Keeping them
  distinct is what makes a future revert-to-prior-accepted possible without
  touching the state machine.
- **`working_version_id`** points at the page's single in-flight version (at most
  one at a time) or is `NULL`. It *is* the UI-lock predicate: the page is locked
  iff `working_version_id is not None`. Fork sets it; Save and Cancel clear it.
- **`PageVersion.status`** is the generation state machine (below). Note: we
  *removed* `Widget.status` earlier; generation status belongs on the version, not
  the widget — this is where it lands.

### Status state machine

```
ACCEPTED (published) ──fork(copy widgets + convo)──▶ IN_PROGRESS
                                                       │   │
                                           task ok ────┘   └──── task error
                                                   ▼            ▼
                                                 READY        FAILED
                                                   │            │
                                   Save ──▶ ACCEPTED            │
                                   (publish, unlock)            │
                                                   │            │
                       Cancel (IN_PROGRESS/READY/FAILED)        │
                                                   ▼────────────┘
                                               CANCELLED → delete version, revert
```

The **UI is locked only while `IN_PROGRESS`** — the model is actively mutating the
version, so the grid and Send are disabled and the only action is Cancel. Once the run
settles into **review (`READY`/`FAILED`) the draft is the user's again**: they can
**move/resize widgets** and **send follow-up messages to keep iterating on the same
working version** (each follow-up commits the client's current widgets, then re-runs —
see Lifecycle). Save (enabled at `READY`) publishes; Cancel discards. *(Revised from
the original "locked through review / pure publish" idea — manual edits during review
need to be captured, so **publish is not payload-free**: it commits the client's
widgets onto the version before flipping `published_version_id`.)*

| status | grid/Send | meaning | Save | Cancel |
| --- | --- | --- | --- | --- |
| `ACCEPTED` | editable | published/live; manual edits mutate it in place | commits to published | n/a |
| `IN_PROGRESS` | **locked** | task running (live updates + elapsed counter) | disabled | yes → abort, delete, revert |
| `READY` | **editable** | generation succeeded, under review (move/iterate) | **enabled → publish** | yes → delete, revert |
| `FAILED` | **editable** | generation errored (edit / retry via follow-up) | disabled | yes → delete, revert |
| `CANCELLED` | — | user cancelled | — | (transient; version deleted) |

A **send while `IN_PROGRESS`** is rejected (409); a send during review continues on the
working version (no new fork).

## Lifecycle

1. **Chat request** → if there's **no working version**, server forks a new
   `PageVersion`, **seeding its widgets from the client's currently-shown set** (the
   posted widget payload = published widgets + any unsaved manual edits, so
   "manual-tweak-then-ask-AI" is preserved and `merge_widget_content` retires) and
   **copying the prior conversation** into it. If a working version already exists
   (review), it **continues on that version** instead: commit the client's current
   widgets (manual moves) via `save_version_widgets`, `restart_version`
   (`IN_PROGRESS`, reset timing). Either way: `status = IN_PROGRESS`, set
   `working_version_id`, append the user message, enqueue a huey task, return the
   `version_id` at once. A send while already `IN_PROGRESS` → 409.
2. **Task** runs `PageBuilderAgent.run_events` (see `yaffo/page_builder/agent.py`).
   Each `widget_new` / `widget_updated` event is **written to the version's
   widgets** (the widget tool persists now — see *Impacted code*). Each assistant /
   status / error event is appended to the version's conversation. On clean finish
   → `READY`; on exception or `max_tokens` → `FAILED` (+ `error`).
3. **Browser observes** by polling the version (`…/versions/<id>/status`): it gets the
   conversation feed, the status, and re-renders the version's widgets. A **live
   elapsed counter** ticks client-side from `started_at`. The grid + Send are locked
   **only while `IN_PROGRESS`**; on `READY`/`FAILED` polling stops and the draft
   becomes editable (move widgets, send follow-ups → step 1's continue branch).
4. **Save** (enabled only when `status == READY`) → **commit the client's current
   widgets** onto the version (`save_version_widgets`, capturing review-time moves),
   then publish: set `page.published_version_id = version.id`, mark `ACCEPTED`, clear
   `working_version_id`, return to presentation. (The old published version is dropped
   on publish for now — retention is a future option, see open Q3.)
5. **Cancel** → signal the task to stop, then **delete the version** (cascade its
   widgets + conversation), clear `working_version_id`, and revert the UI to the
   previously published version.

## Cancellation

Cooperative, two levels:

- **Between agent iterations** — the loop in `run_events` checks a cancel flag
  (e.g. re-read `version.status` or a Redis/db flag) at the top of each `while`
  iteration and stops. Cheap, already has the structure (`while iterations <
  max_iterations`).
- **Mid-model-call** — a single call can be 556s, so iteration-boundary checks
  aren't enough for responsive cancel. The model client currently does
  `with client.messages.stream(...) as stream: stream.get_final_message()`. To make
  cancel responsive, iterate the stream events and check the flag per chunk,
  aborting the stream when cancelled. **Document caveat:** without this, Cancel
  won't take effect until the in-flight call returns.

On cancel: set `status = CANCELLED`, delete the version + children, clear
`working_version_id`, and leave `published_version_id` untouched (UI snaps back to
the published version).

## UI decisions (yours)

- **Hide versioning from the user.** No version picker / history UI — "more
  confusing than helpful." Versions are an internal mechanism for durable
  in-progress generation, cancel/rollback, and conversation scoping.
- **Lock the page to the working version** from fork until the user resolves it —
  what's shown stays consistent with what the model is mutating; the only exits are
  Save or Cancel (no manual editing mid-generation/review).
- **Live elapsed counter** while `IN_PROGRESS` (from `started_at`), frozen on
  terminal status.
- **Save** disabled unless the latest generation is `READY`; it accepts the working
  version (→ `ACCEPTED`) and publishes it.
- **Cancel** deletes the working version and returns to the previous (published)
  version.

## What this changes in the code

- **`yaffo/db/models.py`** — add `PageVersion`; move `Widget.page_id` →
  `version_id` and `Conversation.page_id` → `version_id`; add
  `CustomPage.published_version_id` **and `working_version_id`**. Add a
  `PAGE_VERSION_STATUS_*` set: `IN_PROGRESS`, `READY`, `FAILED`, `ACCEPTED`,
  `CANCELLED`.
- **`yaffo/db/repositories/custom_page_repository.py`** — version-aware reads;
  `fork_version(page_id, widgets)` (seed widgets from the posted set + copy the
  prior conversation, set `working_version_id`), `publish_version(version_id)` (no
  payload: mark `ACCEPTED`, set `published_version_id`, clear `working_version_id`),
  `delete_version` (cancel/fail), conversation/widget writes scoped to a version.
  `create_page` mints the initial empty `ACCEPTED` version. `save_page_widgets`
  becomes "write widgets into version N" (the published version for manual edits).
- **`yaffo/page_builder/tool_providers/widget_tool.py`** — the widget tool
  **persists to the working version** now instead of returning non-persisting
  `WidgetDraft`s. (Or: keep returning drafts, and the *task* writes them to the
  version — decide. Persisting in the tool is simpler; drafts-in-task keeps the
  tool pure.) `merge_widget_content` likely goes away.
- **`yaffo/page_builder/agent.py`** — add the cancel check at the loop boundary.
- **`yaffo/page_builder/model_clients/model_client.py`** — optional: stream-level
  cancel (check flag per chunk) for responsive Cancel during long calls.
- **`yaffo/routes/pages.py`** — `chat` becomes *enqueue + return version_id* (no
  NDJSON stream); add `…/versions/<id>/status` (poll), `…/versions/<id>/cancel`,
  and Save publishes a version. Reuse the existing **huey + `Job` infra** (the
  `index_photos.js` polling pattern is the model to copy for the client).
- **`yaffo/static/pages/grid.js`** — replace the NDJSON reader with a poll loop +
  elapsed timer; Save/Cancel call the new endpoints.

## Open questions / decisions to make next weekend

1. ✅ **Manual edits vs versions** — *resolved: only AI requests fork.* Drag/resize/
   manual-add keep buffering client-side and commit via `save_page_widgets` onto the
   page's published (`ACCEPTED`) version in place; no version is created. Only a chat
   request forks a working version. Manual and AI paths stay distinct (smaller blast
   radius than "design mode always forks").
2. ✅ **Conversation continuity across versions** — *resolved: copy on fork.* Forking
   snapshots the prior conversation into the new version, so follow-ups have context
   and the transcript reads continuously. Conversation is version-scoped.
3. **Retention.** Cancelled versions are deleted; superseded *published* versions are
   currently deleted on publish too. Keeping them for a future revert (the
   `GenWidgetVersion` idea in `ai-page-builder.md`) is left open — `parent_version_id`
   leaves the door open without touching the state machine. UI stays hidden either way.
4. **Poll vs SSE.** Polling matches the existing jobs pattern and is robust; SSE
   would be snappier. Start with polling; SSE later if the latency annoys.
5. **Where does the cancel flag live** — re-read `version.status` from the DB each
   iteration (simple, a little chatty) vs a Redis/in-memory flag.

## Suggested build order

1. **Models + repo**: `PageVersion`, re-scope `Widget`/`Conversation`,
   `published_version_id`, `fork`/`publish`/`delete_version`. Migrate the existing
   tests. (Pure backend, testable alone — like the data-query engine was.)
2. **Task**: a huey task that runs the agent against a version, writes
   widgets/conversation, transitions status. No UI yet — drive it from a test/script.
3. **Routes**: `chat` → enqueue; `status` poll; `cancel`; Save → publish.
4. **UI**: poll loop, elapsed counter, Save-gating, Cancel.
5. **Responsive cancel** (stream-level) + retention/revert — last, optional.

The win to keep in mind: step 1 alone already makes a run **survivable** — the
PageVersion is the durable draft that the 556s run was missing.