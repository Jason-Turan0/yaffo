# AI Page Builder — Design Reference

A feature where users build their own pages out of AI-generated blocks. The user
describes what they want ("a polaroid wall of our Maine trip in summer 2023"),
Claude drafts the page, and the user arranges and resizes blocks on a drag-and-drop
grid. Blocks render free-form HTML/CSS/JS, but the photo data they show is always
resolved server-side.

## Core principle: presentation is free, data is constrained

The single idea the whole design rests on:

> **Claude controls *presentation* (free-form HTML/CSS/JS). The server controls
> *data* (a validated, declarative query, resolved against the DB server-side).**

This is how "free-form HTML/JS" stays safe in an app holding private photos.
Claude writes how a page *looks* and proposes *what* it wants to show, but it
never touches the database and never has a live data channel. A hallucinated
field or a malicious snippet fails validation or renders against nothing — it
can't leak or break anything.

|                          | Decides *what* photos        | Decides *how* they look | Touches the DB |
| ------------------------ | ---------------------------- | ----------------------- | -------------- |
| **Claude**               | proposes a `data_query`      | writes HTML/CSS/JS      | never          |
| **Server**               | validates + runs the query   | injects the result      | only place     |

## Concepts

- **Page** — a named, saved canvas with an optional top-level "theme" prompt and a
  grid layout. Belongs to a user.
- **Block** — an independently-generated, independently-editable unit. Has a
  prompt, a `data_query`, generated `html`/`css`/`js`, a grid position/size, and a
  version history.
- **Data query** — a declarative spec (persons, tags, location, date range, limit,
  …) that Claude proposes and the server validates and resolves into real photos.
- **Layout** — where each block sits and how big it is on a responsive grid.

## How data fetching works (the loop)

Claude generates the data query *as part of* each block — it is not authored
separately. The loop:

### 1. Claude is told the data vocabulary (system prompt)

```
To get photos, emit a `data_query` object. Available filter fields:
  persons:   list of names           tags:     list of tag names
  location:  string                  year / month / date_from / date_to
  has_faces: bool                    limit:    int (max 200)
  order_by:  "date" | "random"

Each photo you receive back has this shape:
  { id, url, thumb_url, taken_at, location, persons: [...], tags: [...], width, height }
```

Claude knows the *menu* of what it can request and the *shape* of what comes back.
It does not know the database.

### 2. Claude returns one structured object per block

```json
{
  "data_query": {
    "location": "Maine",
    "date_from": "2023-06-01",
    "date_to": "2023-08-31",
    "order_by": "date",
    "limit": 60
  },
  "html": "<div class='wall'>...</div>",
  "css": ".polaroid { transform: rotate(...) }",
  "js": "window.__DATA__.photos.forEach(p => { /* build polaroids */ })"
}
```

The JS references `window.__DATA__.photos` — but it has no idea what photos those
are yet. It is written against the *promised shape*, not against actual data.

### 3. The server validates, resolves, and serializes (the only DB access)

```python
query  = validate_data_query(block.data_query)   # unknown field / bad type -> fail closed
photos = resolve_query(query)                     # declarative spec -> SQLAlchemy
data   = {"photos": [p.to_page_dict() for p in photos]}   # to the promised shape
```

### 4. The result is injected into the sandboxed block at render time

```html
<script>window.__DATA__ = {{ data | tojson }};</script>
<script>{{ block.js }}</script>   <!-- Claude's presentation code runs against real data -->
```

Because Claude writes its JS against the *promised shape* before seeing real data,
`to_page_dict()` is a **hard contract**: if the system prompt says a photo has
`thumb_url`, the serializer must always produce it or the generated JS breaks.
Keep this contract stable and versioned — it is the heart of the feature, shared
by every block.

## Sandboxing & security

Each block renders inside its **own iframe**, deliberately *without*
`allow-same-origin`:

```html
<iframe sandbox="allow-scripts"
        csp="default-src 'none'; img-src {{ photo_origin }};
             style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'">
```

- **No `allow-same-origin`** → the frame is a null origin: it cannot read the
  parent's cookies, session, or `localStorage`.
- **`connect-src 'none'`** → generated JS cannot `fetch`/`XHR`/`WebSocket`
  anywhere. It cannot phone home or exfiltrate, even if buggy or adversarial.
- **`img-src` limited** to the photo origin → images load, nothing else does.
- All photo data is **pre-injected** (step 4); there is no live data channel, so
  there is nothing for untrusted JS to abuse.

This is what reconciles "free-form HTML/JS" with private photos: freedom in
layout, zero freedom in data access.

## Drag-and-drop grid layout

Pages are arranged on a responsive grid (target: 12 columns). Each block stores
its position and size; the user drags to reposition and drags edges to resize.

```
┌─ Page: "Summer 2023"   theme: warm, scrapbook ───────────────────────┐
│  cols →  1   2   3   4   5   6   7   8   9  10  11  12                 │
│        ┌───────────────────────────┐ ┌─────────────────┐             │
│        │ Block 1  (hero)           │ │ Block 2  (map)   │   rows      │
│        │ x:0 w:8 h:2               │ │ x:8 w:4 h:2      │    ↓        │
│        └───────────────────────────┘ └─────────────────┘             │
│        ┌─────────────┐ ┌─────────────┐ ┌─────────────┐               │
│        │ Block 3     │ │ Block 4     │ │ Block 5     │               │
│        │ x:0 w:4 h:3 │ │ x:4 w:4 h:3 │ │ x:8 w:4 h:3 │               │
│        └─────────────┘ └─────────────┘ └─────────────┘               │
│        [ + Add block ]   [ ✨ Regenerate whole page from prompt ]     │
└────────────────────────────────────────────────────────────────────────┘
```

### Layout mechanics

- **Grid coordinates per block**: `grid_x`, `grid_y`, `grid_w`, `grid_h` (column /
  row units), stored on the block. The block's iframe fills its grid cell.
- **Server is the source of truth**. Drag-and-drop is inherently client-side JS
  (a library such as Gridstack.js / Muuri), implemented as a namespaced module
  (`window.PHOTO_ORGANIZER.initPageGrid`) per project convention. On drop/resize,
  the module commits the new coordinates to the server (HTMX/`fetch` POST); the
  layout is never client-only state.
- **Responsive fallback**: grid coordinates target the wide layout; below a
  breakpoint, blocks stack to a single column in `grid_y` order. Store wide-layout
  coords only; derive the mobile stack — don't ask users to lay out twice.

### Gotcha: iframes swallow pointer events

A block's content lives in an iframe, and iframes capture mouse events, which
breaks dragging *over* a block. Standard fix: during a drag/resize gesture, set
`pointer-events: none` on all block iframes (or overlay a transparent shim), then
restore on drop. The grid module owns this.

## Authoring experience (prompt + editable blocks)

1. User types a top-level prompt → Claude drafts a **multi-block plan**
   (per-block titles, prompts, data queries, and a sensible starting layout).
2. Blocks generate asynchronously; the user watches them fill in.
3. The user can, per block: **edit the prompt + regenerate**, **edit the data
   query**, **revert to a prior version**, **delete**, or **add** a block — and
   **drag/resize** anything on the grid.

Every block action is a single HTMX endpoint taking
`hx-vals='{"action":"regenerate","block_id":...}'` and returning the re-rendered
block fragment — the same pattern as `remove_duplicates_form.html`. Generation is
slow, so regenerate kicks off a **Job** (reusing the existing `Job`/`JobResult` +
polling and the `job_status` component); the block shows job status until done.

## Data model

```python
class GenPage(db.Model):
    # id, title, theme_prompt, owner, created_at, updated_at

class GenPageBlock(db.Model):
    # id, page_id, order, prompt,
    # data_query (JSON), html, css, js, status, job_id,
    # grid_x, grid_y, grid_w, grid_h

class GenBlockVersion(db.Model):
    # id, block_id, html, css, js, data_query, created_at
    # regeneration is non-destructive: snapshot before overwrite, allow revert
```

Versioning matters because regeneration is a dice roll — users need to revert to a
block they preferred.

## Claude integration

- **Structured output / tool use**: Claude returns `{plan: [...]}` for the page
  draft, and `{data_query, html, css, js}` per block — schema-enforced so output
  is always parseable.
- **Validate `data_query`** against an allow-list of fields before resolving;
  fail closed on anything unknown.
- **Prompt caching is essential**: the system prompt (block contract, available
  fields, photo-shape contract, examples) is large and identical across every
  block and page generation. Cache it for a big cost/latency win, since the
  feature does many small generations. Build the integration with the `claude-api`
  skill (it bakes in caching and current model IDs).

## API key storage

The app is distributed as a native desktop app (Briefcase) on Mac/Windows, so the
API key is kept in the **OS credential vault**, never in the database or a file.
Use the cross-platform [`keyring`](https://pypi.org/project/keyring/) library,
which backs onto the native store per platform:

- **macOS** → Keychain
- **Windows** → Credential Locker
- **Linux desktop** → Secret Service (libsecret / KWallet)

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
   Linux container has no keychain backend, which is exactly why env var stays.
2. **OS keychain via `keyring`** — the local desktop path; set/replaced/cleared
   from the Settings UI.

If neither is set, the feature is disabled with a clear message rather than
failing mid-generation.

### Settings UI

The Settings page shows **presence only** — "Key configured ✓ / Not set" with
**Set / Replace / Clear** controls. The value is never sent back to the browser,
so there is no secret to mask or render. (Today's `ApplicationSettings` store and
its UI hold only non-sensitive filesystem paths and render values verbatim — the
key deliberately does **not** go there.)

### Dependencies

Add `keyring` and `anthropic` to `setup.py` `install_requires`.

## Build phases (smallest shippable first)

1. **Single sandboxed block** — one prompt → one generated iframe with injected
   data. Proves the sandbox + render loop end to end.
2. **Multi-block page + editable HTMX shell** — add / reorder / regenerate /
   revert.
3. **Drag-and-drop grid layout** — coordinates, persistence, iframe pointer-event
   handling, responsive fallback.
4. **Data-query engine** — `validate_data_query` + `resolve_query` +
   `to_page_dict`. Reusable and testable on its own; it is the heart of the system.
5. Model API Key management
6. **Page-level "draft from one prompt"** — orchestration that plans and fans out
   block generation.

## Open questions

- **Static vs. live blocks.** Pre-injected data is the safe default and covers
  rich static layouts. A block that's *interactive* against live data (in-page
  search, infinite scroll) would need a brokered `postMessage` data channel and a
  looser sandbox. Deferred unless interactivity is a goal.
- **Sharing / export.** Single-user/self-hosted keeps the threat model gentle. If
  pages are ever shared or exported to other people, the sandbox CSP and the
  "whose photos can a query touch" rules get much stricter. Out of scope until
  sharing is a requirement.