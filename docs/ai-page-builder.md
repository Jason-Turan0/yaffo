# AI Page Builder — Design Reference

A feature where users build their own pages out of AI-generated **widgets**. The
user describes what they want ("a polaroid wall of our Maine trip in summer
2023"), the model drafts widgets, and the user arranges and resizes them on a
drag-and-drop grid. Widgets render free-form HTML/CSS/JS, but the photo data they
show is always resolved server-side.

> **Status:** a working UI-first prototype exists. Page/widget state lives in an
> in-memory stub store (`yaffo/page_builder/stub_store.py`); data resolution and
> model generation are mocked. The data contract, schema, and the
> browser/server plumbing below are real and exercised — only the DB queries and
> the Claude call are stubbed.

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

- **Page** — a named, saved canvas with an optional `theme_prompt`, a `show_title`
  flag, an ordered set of widgets, and a page-level assistant conversation.
- **Widget** — an independently-generated, independently-editable unit with a
  `title`, a `data_query`, generated `html`/`css`/`js`, persisted `state`, and a
  grid position/size. Renders in its own sandboxed iframe.
- **data_query** — a dict of **named queries** the widget needs (see below).
- **Conversation** — page-level chat with the assistant that generates/edits
  widgets (`messages` on the page).

## The data_query schema (named, multi-source queries)

`data_query` is a dict keyed by **query name**; each value is one query with a
`source` plus that source's filters:

```json
{
  "maine_photos": { "source": "photos", "location": "Maine", "limit": 9 },
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
versioned — they are the heart of the feature. The real engine becomes
`validate_data_query` (per-source allow-list) → `resolve_query` (→ SQLAlchemy) →
`to_*_dict` serializers, one per source. In the prototype these are
`resolve_data` / `resolve_query` over fake data.

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
default-src 'none'; img-src <app-origin> data:;
style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'
```

- **No `allow-same-origin`** → null origin: can't read parent cookies/session.
- **`connect-src 'none'`** → generated JS cannot `fetch`/`XHR`/`WebSocket`. It
  can't phone home or exfiltrate, even if buggy or adversarial.
- **`img-src <app-origin> data:`** → photos load from the app's `/photos/<id>`
  route; nothing else does.

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
| `yaffo:query` `{requestId, query}` | `POST .../widgets/<id>/query` → `resolve_query` | `yaffo:result` `{requestId, data}` to sender |
| `yaffo:publish` `{topic, payload}` | fan out to **every** widget iframe | `yaffo:event` `{topic, payload}` to all |
| `yaffo:state` `{state}` | `POST .../widgets/<id>/state` (persist) | — (fire-and-forget) |

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

## Authoring experience

Pages appear as **tabs in the top nav**, with a `+` tab to create one. A page has
two modes:

- **Presentation** (`GET /pages/<id>`) — read-only; clean body; widgets laid out
  on a static grid. Edit affordance is a **pencil on the active page's nav tab**
  (no in-page chrome). The page title shows only if `show_title` is set.
- **Design** (`GET /pages/<id>/design`) — a **left editor panel** (page details:
  title / description / show-title, grouped Add-widget / Save / Delete actions, and
  the **conversation**) beside the **widget grid canvas** on the right.

A page with ≥1 widget opens in presentation; an empty one redirects to design.

### Single Save + autosave-free layout

**Generation is non-destructive: nothing the agent produces is written until the
user clicks Save.** Generated and edited widgets stream to the browser as
*drafts* the client holds in memory; one **Save** commits title, description,
`show_title`, **and** the full widget set — content *and* layout (incl. per-widget
titles) — in a single `POST /pages/<id>/update`, then returns to presentation.
Dragging/resizing buffers client-side until Save too (no silent autosave).

The client is the **source of truth for the widget set on Save**: each entry
carries layout always, plus content for widgets the client holds as drafts;
entries that reference an untouched saved widget send layout only (the server
keeps its stored content), and widgets absent from the payload are dropped.
`save_page_widgets` reconciles add/edit/delete/reorder in one shot. Widgets are
identified by **GUID** (minted server-side by the tool, or client-side for manual
adds) so a draft's id is stable from creation through Save.

### Conversation → generation (streamed, non-persisting)

The page-level conversation drives generation. `POST /pages/<id>/chat` appends the
user message, runs the agent loop (`PageBuilderAgent.run_events`), and **streams
progress back as newline-delimited JSON** (`application/x-ndjson`) so the page
fills in live during the slow multi-step run. The `create_widget`/`update_widget`
tools **do not touch the store** — each returns a `ToolResult` whose `host_data` is
the widget content, which the route streams. Records:

| record | meaning | client action |
| ------ | ------- | ------------- |
| `{event:"message", content}` | an assistant turn (persisted) | append a chat bubble |
| `{event:"status", text}` | a tool started ("Creating widget…") | update the pinned status bubble |
| `{event:"widget_new", widget}` | the agent drafted a widget (full content) | `POST .../widgets/preview` → set `iframe.srcdoc`, drop on the grid |
| `{event:"widget_updated", widget}` | the agent edited a widget (full content) | re-render the draft in place |
| `{event:"done"}` / `{event:"error",…}` | run finished | remove the status bubble |

The stream carries widget **content**, never an id-to-fetch: the client holds each
as a draft and renders it via the **preview** route (below), which resolves the
widget's data server-side and returns the sandboxed frame for `srcdoc`. So data
resolution stays server-side (the security model holds) while *nothing is
persisted* — generated widgets exist only in the browser until Save. The stream
lives inside the one POST: navigating away abandons the in-flight turn (its drafts
are simply never saved).

When un-stubbed, only the model call (→ Claude) and the `resolve_*` functions
(→ DB) change; the conversation/render/broker plumbing stays.

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

The prototype stub mirrors the eventual SQLAlchemy models:

```python
class GenPage:     # id, title, theme_prompt, show_title, widgets[], messages[], timestamps
class GenWidget:   # id (GUID), title, prompt, data_query(JSON, named queries), state(JSON),
                   #   html, css, js, status, grid_x/y/w/h
class GenMessage:  # role (user|assistant), content
```

`GenWidget` separates three concerns cleanly — **`data_query`** (what data, author
/ AI-defined), **`state`** (runtime UI state, user-driven, persisted), and
**`html/css/js`** (presentation). For the real model add `owner`, ordering, and a
`GenWidgetVersion` snapshot table (regeneration is a dice roll — keep versions so
users can revert).

## Routes

```
GET  /pages/<id>                        presentation (redirects to design if empty)
GET  /pages/<id>/design                 editor
POST /pages/<id>                        create
POST /pages/<id>/update                 save title/desc/show_title + layout
POST /pages/<id>/delete                 delete
POST /pages/<id>/widgets                add widget (returns widget fragment)
POST /pages/<id>/widgets/preview        render a draft's grid-item shell from posted content (srcdoc frame, no persist)
GET  /pages/<id>/widgets/<wid>/frame    sandboxed render document for a saved widget (sets CSP)
POST /pages/<id>/widgets/<wid>/delete   remove
POST /pages/<id>/widgets/<wid>/query    live query  (broker: yaffo:query)
POST /pages/<id>/widgets/<wid>/state    persist state (broker: yaffo:state)
POST /pages/<id>/chat                   conversation → agent run, streamed as NDJSON (widget content)
```

Page tabs are injected into every template via a context processor.

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

The app is distributed as a native desktop app (Briefcase) on Mac/Windows, so the
API key is kept in the **OS credential vault**, never in the database or a file.
Use the cross-platform [`keyring`](https://pypi.org/project/keyring/) library:

- **macOS** → Keychain · **Windows** → Credential Locker · **Linux** → Secret Service

```python
import keyring
keyring.set_password("yaffo", "anthropic_api_key", key)   # Settings UI: Set / Replace
keyring.get_password("yaffo", "anthropic_api_key")         # resolver
keyring.delete_password("yaffo", "anthropic_api_key")      # Settings UI: Clear
```

### Resolution order (env wins)

A single resolver `get_anthropic_api_key()` is the only thing that reads the key:

1. **`ANTHROPIC_API_KEY` environment variable** — wins. The path for the headless
   GCP demo (Secret Manager → container env; see
   `docs/deployment/gcp-demo-architecture.md`), CI, and power users. A headless
   container has no keychain backend, which is exactly why env var stays.
2. **OS keychain via `keyring`** — the local desktop path; set/cleared from the
   Settings UI.

If neither is set, the feature is disabled with a clear message.

### Settings UI

Shows **presence only** — "Key configured ✓ / Not set" with **Set / Replace /
Clear**. The value is never sent to the browser. It deliberately does **not** go in
`ApplicationSettings`, which holds only non-sensitive paths and renders verbatim.

### Dependencies

Add `keyring` and `anthropic` to `setup.py` `install_requires`. (`gridstack` and
the other front-end libs are vendored under `static/vendor/`; refresh via
`inv update-vendor`.)

## Build phases (smallest shippable first)

1. ✅ **Sandboxed widget + grid + editor + conversation** — UI-first prototype with
   stubbed data and generation (done).
2. **Data-query engine** — `validate_data_query` (per source) + `resolve_query`
   (→ SQLAlchemy) + `to_*_dict`. Replaces the stub resolvers; the heart of the
   system. Pure backend, testable alone.
3. **Model API key management** — keychain + env resolver + Settings UI.
4. **Real generation** — wire the conversation to Claude (replaces
   `generate_widget`); structured output, validation, prompt caching, Jobs for
   slow calls + versioning.
5. **Persistence** — move the stub store to SQLAlchemy models (+ version table).

## Open questions / future

- **Per-viewer vs. authored state.** `state` is currently per-widget (single
  user). Sharing would need to split authored defaults from per-viewer state.
- **Sharing / export.** Single-user/self-hosted keeps the threat model gentle. If
  pages are shared or exported, the sandbox CSP and "whose photos can a query
  touch" rules get much stricter. Out of scope until sharing is a requirement.
- **Declared pub/sub contract.** Topics a widget publishes/subscribes are
  currently implicit in its JS. Declaring them on the widget would let the model
  wire widgets together intentionally and allow validation.
- **Map tiles.** A real tiled map needs network the sandbox forbids
  (`connect-src 'none'`); either keep stylized/pin renderings or special-case a
  tile origin in the CSP.