# AI Page Builder — Design Reference

A feature where users build their own pages out of AI-generated **widgets**. The
user describes what they want ("a polaroid wall of our Maine trip in summer
2023"), the model drafts widgets, and the user arranges and resizes them on a
drag-and-drop grid. Widgets render free-form HTML/CSS/JS, but the photo data they
show is always resolved server-side.

> **Status (2026-06-21): built and working.** Real persistence (SQLAlchemy —
> `CustomPage` / `PageVersion` / `Widget` / `Conversation`), a real data-query
> engine (`data_query_repository` + serializers), and real Claude generation
> (`yaffo/site_agents/`) are all in place; the old in-memory stub store is gone.
> Generation runs **asynchronously** as a durable `PageVersion` on a background
> task (`yaffo/taskq`), with the browser polling for progress — see *Async
> generation via PageVersion*. This doc consolidates the original feature reference
> and the async-generation design (now shipped) into one.

## Core principle: presentation is free, data is constrained

The single idea the whole design rests on:

> **The model controls *presentation* (free-form HTML/CSS/JS). The server
> controls *data* (validated, declarative queries resolved server-side).**

This is how "free-form HTML/JS" stays safe in an app holding private photos. The
model writes how a widget *looks* and proposes *what* it wants to show, but it
never touches the database and never has a direct network channel. A hallucinated
field or a malicious snippet fails validation or renders against nothing — it
can't leak or break anything.

|             | Decides *what* data        | Decides *how* it looks | Touches the DB / network |
| ----------- | -------------------------- | ---------------------- | ------------------------ |
| **Model**   | proposes named `data_query`| writes HTML/CSS/JS      | never                    |
| **Server**  | validates + runs queries   | injects results         | only place               |

## Concepts

- **Page** (`CustomPage`) — a named, saved canvas with an optional subtitle, a
  `show_title` flag, a pointer to its **published version** (shown in
  presentation) and an optional **working version** (the in-flight draft).
- **PageVersion** — the durable unit of work. Every page has exactly one
  `ACCEPTED` version that owns its live widgets; an AI request **forks** a new
  version that the generation task fills in. Hidden from the user (no version
  picker) — purely the mechanism for durable generation, cancel/rollback, and
  conversation scoping. See *Async generation via PageVersion*.
- **Widget** — an independently-generated, independently-editable unit with a
  `title`, a `data_query`, generated `html`/`css`/`js`, persisted `state`, and a
  grid position/size. Scoped to a **version**. Renders in its own sandboxed iframe.
- **data_query** — a dict of **named queries** the widget needs (see below).
- **Conversation** — version-scoped chat with the assistant that generates/edits
  widgets. Forking copies the prior conversation so follow-ups read continuously.

## The data_query schema (named, multi-source queries)

`data_query` is a dict keyed by **query name**; each value is one query with a
`source` plus that source's filters:

```json
{
  "maine_photos": { "source": "media_items", "location": "Maine", "limit": 9 },
  "facets":       { "source": "facets" }
}
```

- A widget can declare **several independent queries**, each hitting one source,
  each with its own filters/limit.
- The server resolves each independently and returns `{query_name: rows}`, so the
  widget reads `window.__DATA__.maine_photos`, `window.__DATA__.facets`, etc., and
  stitches the named results together.
- The query *name* is the contract between the data and the code: the model emits
  named queries and writes JS that reads those same names.

### Sources and their shapes (the "promised shape" contract)

Each source returns a fixed shape the widget code is written against:

| source      | returns                                                              |
| ----------- | ------------------------------------------------------------------- |
| `photos`    | `[{id, url, thumb_url, taken_at, year, location, persons[], tags[], width, height}]` |
| `persons`   | `[{name, photo_count, face_thumb_url}]`                             |
| `locations` | `[{name, lat, lon, count}]`                                         |
| `tags`      | `[{name, count}]`                                                   |
| `stats`     | `{photos, people, locations, years}`                               |
| `facets`    | `{years[], tags[], locations[], persons[]}` — for in-widget filter controls |

These shapes are a **hard contract**: if the spec says a photo has `thumb_url`,
the serializer must always produce it or generated JS breaks. Keep them stable and
versioned — they are the heart of the feature. The engine is
`data_query_repository` (`validate_data_query` → `resolve_query`/`resolve_data_query`
→ SQLAlchemy → `to_*_dict` serializers, one per source).

## Rendering: the sandboxed iframe

Each widget renders in its **own iframe**, deliberately *without*
`allow-same-origin`, served from `/pages/<id>/widgets/<id>/frame`:

```html
<iframe class="widget-frame" data-widget-id="..." sandbox="allow-scripts" src=".../frame">
```

The frame document injects the widget's data and state, then runs its code:

```html
<style>{{ widget.css }}</style>
{{ widget.html }}
<script>
  window.__DATA__  = {{ data | tojson }};   // {query_name: rows}, per this iframe
  window.__STATE__ = {{ widget.state | tojson }};
</script>
<script>{{ widget.js }}</script>
```

Each iframe is a separate document, so **each has its own `window`, its own
`__DATA__`/`__STATE__`** — widgets are fully isolated from each other and the
parent.

The frame response sets a strict CSP (origin injected at request time, since the
sandboxed frame is a null origin and `'self'` won't match):

```
default-src 'none';
img-src <app-origin> data: https://tile.openstreetmap.org https://*.tile.openstreetmap.org;
style-src 'unsafe-inline' <app-origin>;
script-src 'unsafe-inline' <app-origin>;
connect-src 'none'
```

- **No `allow-same-origin`** → null origin: can't read parent cookies/session.
- **`connect-src 'none'`** → generated JS still cannot `fetch`/`XHR`/`WebSocket`. It
  can't phone home or exfiltrate, even if buggy or adversarial — this is the load-bearing
  rule and stays closed.
- **`script-src`/`style-src` add `<app-origin>`** → a widget may load the app's *own
  vendored* libraries (e.g. OpenLayers, `/static/vendor/ol`) — our code, not the model's.
  Inline (`'unsafe-inline'`) still works (a host-source doesn't disable it; only a
  nonce/hash would).
- **`img-src <app-origin> data:` + OSM tile hosts** → photos load from `/photos/<id>`,
  and OpenStreetMap basemap tiles load as images (the one third-party origin allowed —
  raster tiles are `<img>`, so `connect-src` stays `'none'`). A widget reaching OSM
  directly is a deliberate, accepted loosening (the alternative, a server-side tile proxy,
  keeps widgets network-free but was not chosen).

This reconciles free-form HTML/JS with private photos: freedom in layout, zero
freedom in data access.

## Widget interactivity: the parent broker

Because the iframe can't `fetch` and can't see other widgets, anything beyond
"render once" goes through the **parent broker** (`initWidgetBroker`) over
`postMessage` — which CSP does *not* block. There are three interactivity tiers:

1. **Static** — everything in `data_query`, render once from `window.__DATA__`.
   (Filterable widgets pre-load a superset + `facets` and filter client-side.)
2. **Live** — run follow-up queries on demand after load.
3. **Connected** — publish/subscribe between widgets.

The broker relays four message types over the one channel:

| message (iframe → parent) | parent action | reply (parent → iframe) |
| ------------------------- | ------------- | ----------------------- |
| `yaffo:query` `{requestId, query}` | `POST .../versions/<id>/widgets/<id>/query` → `resolve_query` | `yaffo:result` `{requestId, data}` to sender |
| `yaffo:publish` `{topic, payload}` | fan out to **every** widget iframe | `yaffo:event` `{topic, payload}` to all |
| `yaffo:state` `{state}` | `POST .../versions/<id>/widgets/<id>/state` (persist) | — (fire-and-forget) |

The parent identifies the sending iframe via `event.source` + its
`data-widget-id`. The broker runs in both design and presentation modes (live
widgets work in both).

### Widget state

Each widget owns a persisted `state` dict (its filter selection, sort, current
tab…). Read it from `window.__STATE__` on load; save it by posting `yaffo:state`.
It's re-injected on the next render, so the widget restores itself. State is
opaque widget-authored JSON (validate size/JSON only — no allow-list needed,
unlike `data_query`).

### Example: a global filter that filters other widgets

- **Global filter** widget pulls `facets`, renders Location/Year selects. On
  change it `yaffo:publish`es `{topic:'filter', payload:{location, year}}` *and*
  saves that as its `state` (so it restores on reload).
- **Linked gallery** widgets subscribe to the `filter` topic; on each event they
  run a `yaffo:query` with the new filter and re-render.

```
Global filter change ─publish 'filter'→ broker ─fan out→ each Linked gallery
   Linked gallery ─query{photos,location,year}→ broker ─fetch→ server ─rows→ re-render
```

## Async generation via PageVersion

Generation runs are long — one observed run was **682s across 5 model calls**, a
single `create_widget` producing **40,444 output tokens in 556s**. Running that
inside the HTTP request (the original streamed-NDJSON design) pinned a worker for
nine minutes, hit every timeout layer, and — worst — kept the result only as
browser-held drafts, so a tab close threw away the whole run *and* the spend.

So generation is **off the request thread and onto a durable version**.

### Core idea: the PageVersion *is* the durable draft

When an AI request comes in, **fork a new `PageVersion`** that owns a *copy of the
current widgets*, mark it `IN_PROGRESS`, and run the agent in a **background task**
(`yaffo/taskq`) that writes widgets / conversation / status into that version. The
published page is untouched until the user clicks Save. So:

- The "non-destructive, nothing changes until Save" principle still holds — but at
  the *page* level (the published version is unchanged), not by keeping drafts in
  browser memory.
- The work is **durable**: it survives tab close, reload, disconnect, and request
  timeouts, because it lives in a version row, not the request.
- It's simpler than the old design — no browser-held drafts, no in-request NDJSON
  stream, no client-vs-stored widget merge (the version holds the real widgets).

### Status state machine

```
ACCEPTED (published) ──fork(copy widgets + convo)──▶ IN_PROGRESS
                                                       │   │
                                           task ok ────┘   └──── task error
                                                   ▼            ▼
                                                 READY        FAILED
                                                   │            │
                                   Save ──▶ ACCEPTED            │
                       (publish working version)   │            │
                                                   │            │
                       Cancel (IN_PROGRESS/READY/FAILED)        │
                                                   ▼────────────┘
                                               CANCELLED → delete version, revert
```

The **UI is locked only while `IN_PROGRESS`** — the model is actively mutating the
version, so the grid and Send are disabled and the only action is Cancel. Once the
run settles into review (`READY`/`FAILED`) **the draft is the user's again**: they
can move/resize widgets and send follow-up messages to keep iterating on the same
working version (each follow-up commits the client's current widgets, then re-runs).
Save (enabled at `READY`) publishes; Cancel discards.

| status | grid/Send | meaning | Save | Cancel |
| --- | --- | --- | --- | --- |
| `ACCEPTED` | editable | published/live; manual edits mutate it in place | commits to published | n/a |
| `IN_PROGRESS` | **locked** | task running (live updates + elapsed counter) | disabled | yes → abort, delete, revert |
| `READY` | **editable** | generation succeeded, under review (move/iterate) | **enabled → publish** | yes → delete, revert |
| `FAILED` | **editable** | generation errored (edit / retry via follow-up) | disabled | yes → delete, revert |
| `CANCELLED` | — | user cancelled | — | (transient; version deleted) |

A **send while `IN_PROGRESS`** is rejected (409); a send during review continues on
the working version (no new fork).

### Lifecycle

1. **Chat request** (`POST /pages/<id>/chat`) → if there's **no working version**,
   fork one, **seeding its widgets from the client's currently-shown set** (so
   unsaved manual edits carry in) and **copying the prior conversation**. If a
   working version already exists (review), **continue on it**: commit the
   client's current widgets (`save_version_widgets`, capturing manual moves), then
   `restart_version`. Either way: `status = IN_PROGRESS`, append the user message,
   enqueue the task, and **return `version_id` with `202` at once**. A send while
   `IN_PROGRESS` → `409`.
2. **Task** (`generate_page_task` → `run_generation`) runs the agent
   (`site_agents` agent `run_events`). Each `widget_new`/`widget_updated` event is
   written to the version's widgets (the widget tool persists to the working
   version); each assistant/status/error event is appended to the version's
   conversation. Clean finish → `READY`; exception or `max_tokens` → `FAILED`
   (+ `error`). A `widget_errors` map the client collected from runtime exceptions
   is fed in so the model can repair code that threw.
3. **Browser observes** by polling `GET /pages/<id>/versions/<id>/status`: it gets
   the conversation feed, the status, and re-renders the version's widgets. A live
   **elapsed counter** ticks client-side from `started_at`. Grid + Send are locked
   only while `IN_PROGRESS`; on `READY`/`FAILED` polling stops and the draft
   becomes editable.
4. **Save** (`POST .../versions/<id>/publish`, enabled only at `READY`) → commit
   the client's current widgets onto the version (capturing review-time moves),
   then publish: `page.published_version_id = version.id`, mark `ACCEPTED`, clear
   `working_version_id`, back to presentation.
5. **Cancel** (`POST .../versions/<id>/cancel`) → signal the task to stop, delete
   the version (cascade widgets + conversation), clear `working_version_id`, revert
   the UI to the published version.

### Cancellation

Cooperative. The task re-reads `version.status` from the DB at each agent-loop
iteration (`should_cancel = get_version_status(id) == CANCELLED`, passed into
`run_events`) and stops at the boundary. Cancel signal = the route setting the
version `CANCELLED`.

**Caveat:** a single model call can be 556s, and the boundary check only fires
*between* iterations — so Cancel won't take effect until the in-flight call
returns. Mid-stream cancel (abort the streamed response per chunk) is a future
refinement, not built.

## Authoring experience

Pages appear as **tabs in the top nav**, with a `+` tab to create one. A page has
two modes:

- **Presentation** (`GET /pages/<id>`) — read-only; clean body; the published
  version's widgets laid out on a static grid. Edit affordance is a **pencil on the
  active page's nav tab**. The page title shows only if `show_title` is set.
- **Design** (`GET /pages/<id>/design`) — a **left editor panel** (page details:
  title / description / show-title, grouped actions, and the **conversation**)
  beside the **widget grid canvas** on the right.

A page with ≥1 widget opens in presentation; an empty one redirects to design.

### Manual edits vs. AI requests

Two distinct paths into a page's widgets:

- **Manual** (drag / resize / manual add) buffers client-side and commits via
  `save_version_widgets` onto the page's **published (`ACCEPTED`) version in
  place** — no fork. One **Save** (`POST /pages/<id>/update`) writes title,
  description, `show_title`, and the full widget set (content + layout) at once;
  widgets are identified by **GUID** so a draft's id is stable.
- **AI** (a chat request) **forks a working version** and runs async (above). Only
  AI requests fork — keeping the manual and AI paths distinct.

Either way, **nothing the agent produces affects the live page until the user
clicks Save** — that property now comes from the published version being untouched
while the working version is generated/reviewed, rather than from browser-held
drafts.

## Drag-and-drop grid layout

Widgets sit on a responsive 12-column grid (Gridstack). Each widget stores
`grid_x/y/w/h`; the user drags to reposition and drags a corner to resize.

- **Server is the source of truth** — layout commits on Save. Implemented as a
  namespaced module (`window.PHOTO_ORGANIZER.initDesignGrid` /
  `initPresentationGrid`).
- **Responsive fallback** — coordinates target the wide layout; below 768px
  widgets stack to a single column. Store wide coords only; derive the stack.
- **Gotcha: iframes swallow pointer events.** During a drag/resize gesture the
  grid gets an `is-interacting` class that sets `pointer-events: none` on the
  widget iframes, restored on drop.
- **Gotcha: Gridstack insets each item by its margin on all sides** (including
  outer edges); the grid is pulled out with a negative margin so edge widgets line
  up flush with the panels.

## Data model

```
CustomPage     id, title, subtitle, show_title, timestamps,
               published_version_id → PageVersion   (the live version, shown in presentation)
               working_version_id   → PageVersion   (the in-flight version, or NULL; the UI-lock predicate)

PageVersion    id, page_id, status, parent_version_id (forked-from),
               created_at, started_at, completed_at, error

Widget         id (GUID), title, prompt, data_query (JSON, named queries),
               state (JSON), html, css, js, grid_x/y/w/h, version_id

Conversation   id, role (user|assistant), content, version_id
```

Key relationships and invariants:

- **Widgets and conversation are version-scoped** (`version_id`), not page-scoped.
  A version owns a snapshot of the widget set; presentation renders the published
  version's widgets. There is **no `Widget.status`** — generation status lives on
  the version.
- **Every page always has one `ACCEPTED` version** that owns its live widgets.
  `create_page` mints an initial empty `ACCEPTED` version and sets
  `published_version_id`. A brand-new manual page is just a page whose committed
  version is empty.
- **`published_version_id` vs. the `ACCEPTED` status compose but differ.**
  `ACCEPTED` is the *status* of any saved snapshot; `published_version_id` points
  at the one accepted version currently **live**. Keeping them distinct is what
  leaves a future revert-to-prior-accepted possible without touching the state
  machine.
- **`working_version_id`** is the page's single in-flight version (≤1 at a time) or
  `NULL`. It *is* the UI-lock predicate: locked iff `working_version_id is not
  None`. Fork sets it; Save and Cancel clear it.
- `Widget` separates three concerns cleanly — **`data_query`** (what data,
  AI-defined), **`state`** (runtime UI state, user-driven, persisted), and
  **`html/css/js`** (presentation).

## Routes

```
GET  /pages/<id>                                       presentation (redirects to design if empty)
GET  /pages/<id>/design                                editor
POST /pages                                            create
POST /pages/<id>/update                                save title/desc/show_title + layout (manual, published version)
POST /pages/<id>/delete                                delete
POST /pages/<id>/widgets/preview                       render a draft's grid-item shell from posted content (srcdoc frame, no persist)
GET  /pages/<id>/widgets/<wid>/frame                   sandboxed render document (sets CSP)
POST /pages/<id>/chat                                  start/continue async generation → enqueue task, return version_id (202)
GET  /pages/<id>/versions/<vid>/status                 poll: status + conversation + widgets
POST /pages/<id>/versions/<vid>/cancel                 cancel the run, delete version, revert
POST /pages/<id>/versions/<vid>/publish                Save: commit client widgets, publish the version
POST /pages/<id>/versions/<vid>/widgets/<wid>/delete   remove a widget from the version
POST /pages/<id>/versions/<vid>/widgets/<wid>/query    live query  (broker: yaffo:query)
POST /pages/<id>/versions/<vid>/widgets/<wid>/state    persist state (broker: yaffo:state)
```

Page tabs are injected into every template via a context processor.

## Implementation map

| Concern | Where |
| --- | --- |
| Models + statuses | `yaffo/db/models.py` (`CustomPage`, `PageVersion`, `Widget`, `Conversation`, `PAGE_VERSION_STATUS_*`) |
| Version repo | `yaffo/db/repositories/custom_page_repository.py` (`fork_version`, `restart_version`, `save_version_widgets`, `set_version_status`, `publish_version`, `delete_version`) |
| Data-query engine | `yaffo/db/repositories/data_query_repository.py` (`validate_data_query`, `resolve_query`, `resolve_data_query`) + serializers |
| Agent + tools | `yaffo/site_agents/` (`agent.py` `run_events`, `model_clients/`, `tool_providers/widget_tool.py`) |
| Generation task | `yaffo/background_tasks/tasks/generate_page.py` (`generate_page_task` → `run_generation`) on `yaffo/taskq` |
| Routes | `yaffo/routes/pages.py` |
| Client | `yaffo/static/pages/` (grid + poll loop + elapsed timer + Save/Cancel) |

## Claude integration

- **Structured output / tool use**: Claude returns a page plan and, per widget,
  `{title, data_query, html, css, js}` — schema-enforced so output is parseable.
- **Validate `data_query`** per source against an allow-list before resolving;
  fail closed on unknown sources/fields. The live-query endpoint runs
  AI-influenced queries too, so it shares the same validation — the broker is a
  query surface, not just a render path.
- **Prompt caching is essential**: the system prompt (the source/shape contract,
  examples, broker API) is large and identical across every generation. Cache it.
  Build with the `claude-api` skill (caching + current model IDs).

## API key storage

The app is distributed as a native desktop app (PyInstaller) on Mac/Windows, so the
API key is kept in the **OS credential vault**, never in the database or a file
(`yaffo/site_agents/llm_config.py`, via the cross-platform `keyring` library):

- **macOS** → Keychain · **Windows** → Credential Locker · **Linux** → Secret Service

### Resolution order (env wins)

A single resolver (`llm_config.get_api_key()`) is the only thing that reads the key:

1. **`ANTHROPIC_API_KEY` environment variable** — wins. The path for headless
   deploys (Secret Manager → container env), CI, and power users. A headless
   container has no keychain backend, which is why env var stays. (This is also how
   spawned task workers inherit the key — `prime_subprocess_env` — so a background
   worker never pops a keychain prompt.)
2. **OS keychain via `keyring`** — the local desktop path; set/cleared from the
   Settings UI.

If neither is set, generation is disabled with a clear message (the `/chat` route
returns `400`).

### Settings UI

Shows **presence only** — "Key configured ✓ / Not set" with **Set / Replace /
Clear**. The value is never sent to the browser. It deliberately does **not** go in
`ApplicationSettings`, which holds only non-sensitive paths and renders verbatim.

## Open questions / future

- **Version retention / revert.** Cancelled versions are deleted; the superseded
  published version is dropped on publish too. Keeping prior accepted versions for
  a user-facing revert is left open — `parent_version_id` leaves the door open
  without touching the state machine. (Versioning UI stays hidden either way.)
- **Responsive mid-call cancel.** Today cancel only lands between agent
  iterations; aborting the streamed model response per chunk would make Cancel
  responsive during a long single call. Not built.
- **Poll vs. SSE.** Polling matches the existing jobs pattern and is robust; SSE
  would be snappier. Polling for now; SSE later if the latency annoys.
- **Per-viewer vs. authored state.** `state` is currently per-widget (single
  user). Sharing would need to split authored defaults from per-viewer state.
- **Sharing / export.** Single-user/self-hosted keeps the threat model gentle. If
  pages are shared or exported, the sandbox CSP and "whose photos can a query
  touch" rules get much stricter. Out of scope until sharing is a requirement.
- **Declared pub/sub contract.** Topics a widget publishes/subscribes are
  currently implicit in its JS. Declaring them on the widget would let the model
  wire widgets together intentionally and allow validation.
- ✅ **Map tiles** — *resolved.* The widget frame loads vendored **OpenLayers**
  with an **OpenStreetMap** basemap (OSM tile hosts whitelisted in `img-src`). See
  the `Photo map` widget template. A server-side tile proxy (keeping widgets
  network-free) is the tighter alternative if the direct-to-OSM loosening ever
  needs reversing.
```
