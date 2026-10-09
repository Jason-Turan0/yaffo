# Sync Adapters — Sources and Targets over a Host Sync Engine (Design Sketch)

**Status: proposed, not built. Alternative to [Automation Web Calls](automation-web-calls.md).**
That doc has a custom automation do *everything* for an export (select, diff,
upload, batch, record state) in one Starlark script. This sketch keeps its
primitives (Connections, endpoint grants, `web_request`, `web_upload_media`,
follow-up hosts, the static endpoint detector) and changes who owns what:

- **Yaffo's host owns the sync engine**: diffing, state, batching, retries, rate
  limits, resume, crash recovery, downloads, file writes, and indexing.
- **A small Starlark adapter** owns only the provider-specific part: how to list a
  provider's items (Source) or how to push/remove items (Target).
- **The selection** (what to export) is custom: a Yaffo album, a saved
  `data_query`, or a selector script.

The same engine and contract serve both directions (import and export). Shipped
**presets** are adapters written and reviewed by us, in the same format the AI
builder writes for the long tail. See [Automations](automations.md) for the
sandbox, host API, and run model this extends.

## Why

Split the import and export pipelines by step and almost everything is
provider-independent:

| step | provider-specific? | owner |
|---|---|---|
| Auth (OAuth 2, OAuth 1.0a signing, API key) | yes | Connection preset (host) |
| **List items / delta cursor** (import) | **yes** | **Source adapter** |
| **Map provider JSON → standard record** (import) | **yes** | **Source adapter** |
| **Create album, upload, add, caption, remove** (export) | **yes** | **Target adapter** |
| What to export, routing, captions | user-specific | Selection |
| Diff against last run, remote-id state | no | host |
| Batching, retry, backoff, resume, backfill | no | host |
| Crash recovery / reconcile | no (given a `list_remote` hook) | host |
| Download, validate, write into the library, dedupe | no | host |
| Metadata merge, `date_taken` rules, indexing | no | host |

Writing the whole pipeline per user (the web-calls doc's model) regenerates the
error-prone parts every time: pagination, delta tokens, partial failures, and
the crash window between "uploaded" and "recorded". Writing the whole pipeline
per provider in Python (a classic system automation) means maintaining N
adapters. This design keeps the provider-specific code to roughly 50–150 lines of
Starlark per provider, and lets the host make everything else correct once.

## Architecture

```
                 ┌──────────────── Connection (preset: auth, base_urls, followup_hosts)
                 │
 Source binding  ▼                         Host sync engine                      Library
 ┌─────────────────────┐  list_page()   ┌──────────────────────────────┐  write  ┌──────────┐
 │ Source adapter      │ ─────────────► │ validate records             │ ──────► │ Imports/ │
 │ (preset or custom)  │ ◄───────────── │ dedupe (state map + sha1)    │         │  <src>/  │
 └─────────────────────┘  cursor        │ download → sniff → atomic mv │         └────┬─────┘
                                        │ metadata hints → indexer     │              │ index
                                        └──────────────────────────────┘              ▼
                                                                                 media_items
 Target binding
 ┌─────────────────────┐  select       ┌──────────────────────────────┐  push()   ┌──────────────┐
 │ Selection           │ ────────────► │ diff vs remote map           │ ────────► │ Target       │
 │ album | query |     │  (ids,        │ batch, retry, backoff        │ ◄──────── │ adapter      │
 │ selector script     │   routing,    │ intent log + reconcile       │  results  │ (preset or   │
 └─────────────────────┘   captions)   └──────────────────────────────┘           │  custom)     │
                                                                                  └──────┬───────┘
                                                                                         │ web_request /
                                                                                         ▼ web_upload_media
                                                                                     provider API
```

A **binding** is an automation with a `sync` configuration (a JSON column on
`automations`; see *Data model*). It reuses everything
automations already have: triggers (schedule, events), runs recorded as `Job`s,
run history, enable/disable, and Test. Album-selected targets also react to a
new `album_changed` event (see *Triggers and the `album_changed` event*).

## Concepts

### Connection

Unchanged from [Automation Web Calls](automation-web-calls.md#connection): a named
credential plus `base_urls`, `followup_hosts`, and scopes, with a provider preset.
Adding OAuth 1.0a (Flickr, SmugMug) and AWS SigV4 (S3, R2, B2) means the
connection signs the **whole request**, not just a header. That happens in the
host's request pipeline, so adapters don't notice.

### Adapter

A Starlark module with a literal `ADAPTER` metadata dict and a fixed set of
**hook functions**. It has a `role` (`source` or `target`), and is either a
**preset** (shipped with Yaffo, code-hash pinned, read-only) or **custom**
(written in the builder, owned by the user).

### Binding

Which adapter, through which connection, with what options, on what trigger:

- **Source binding**: adapter + connection + options (e.g. folder, album) +
  **import root** (a media dir and subfolder the host may write into).
- **Target binding**: adapter + connection + options (e.g. album title) +
  **selection**.

### Selection (targets only)

What leaves Yaffo, and where it goes. One of:

| kind | definition | when |
|---|---|---|
| `album` | a Yaffo album id; membership mirrors to the target | simplest; the user curates by hand |
| `query` | a `data_query` dict (validated, stored as data) | "faces include Mom, last 30 days, no screenshots" |
| `script` | a selector Starlark script (read-only automation profile) | routing to several albums, computed captions |

All three produce the same thing: the **desired set**, a list of
`{media_item_id, target, caption}`. `target` is a routing key interpreted by the
adapter (e.g. an album title); `caption` is optional text.

### Sync state

Owned by the host and keyed by binding. Neither adapters nor selectors write it
directly:

- **Item ledger** (the `sync_items` table): the mapping between Yaffo and the
  remote, one row per item.
- **Adapter state** (`runtime.state` in the automation's `sync` JSON): small
  key/values for the adapter (album ids, per-target settings). Adapters read it
  via `ctx["state"]` and change it only by returning a `state_patch` (see
  below), so state commits **atomically with the results it describes**.
- **Cursors** (`runtime.cursors`, same JSON): resume points and delta
  checkpoints, owned by the engine alone.

See *Data model* for the full tables.

## Execution model

### Code layout: one module per adapter, hooks registered in a dict

Three ways to package an adapter's hooks were considered:

| layout | pros | cons |
|---|---|---|
| **A. One module, hooks found by naming convention** (`def list_page`, `def push`) | one code blob, one hash, one endpoint manifest; helpers shared between hooks | discovery by magic names; a misspelled `def pussh` is silently "not implemented" |
| **B. Separate scripts per hook**, one DB row each | each script is tiny and evaluated alone | helpers duplicated (OneDrive's `to_record` is used by `list_page` *and* `list_remote`); N hashes and N manifests to keep in sync for one consent; drafts/publish must move N rows atomically |
| **C. One module, hooks registered explicitly in `ADAPTER["hooks"]`** | everything in A, plus: the hook set is declared, so typos, missing required hooks, and unknown hook names are static errors | `ADAPTER` is no longer pure JSON (it holds function references), so it's read from the AST, not by evaluation |

**Chosen: C.** An adapter is **one Starlark module stored as one code blob**
(with working/published versions, like an automation). Its hooks are top-level
functions, registered by name in the `hooks` dict:

```python
def list_page(cursor, ctx): ...
def to_record(item): ...            # private helper: not in `hooks`, so the host never calls it

ADAPTER = {
    "contract": 1,
    "role": "source",
    "provider": "onedrive",
    "hooks": {"list_page": list_page},
    ...
}
```

Static rules (checked when the builder writes code, and again at publish):

- `ADAPTER` is assigned exactly once at top level, and its value is a literal
  except for `hooks` values, which must be **bare names of top-level `def`s**.
- `hooks` keys must be known for the role. Required: `list_page` (source);
  `push` (target). Optional: `resolve_download` (source); `remove`, `update`,
  `list_remote` (target).
- Capabilities and hooks must agree: `"remove": True` requires a `remove` hook,
  and a `remove` hook without the capability is an error.

The host stores the extracted metadata as JSON (hook values become their
function names), so the UI, the builder prompt, and the consent screen never run
code to learn what an adapter does.

**The selector is a separate script, on purpose.** An adapter is
provider-specific and reusable (presets are shared by every binding); a
selection is user-specific. Keeping them apart means one Google Photos preset
serves every user's "For Mom" rule, and changing the rule never touches (or
re-consents) the adapter's web grants. The selector lives on the binding's
`Automation` row (its `working_code` / `published_code`), which also gives it
the existing builder chat, Test, and publish flow unchanged.

### Hooks are called by the host, one fresh evaluator per call

Today `run_starlark` evaluates a module top to bottom and returns the trailing
expression. Adapters reuse that unchanged: to call a hook, the host evaluates

```
<adapter code>
ADAPTER["hooks"]["list_page"](__args__["cursor"], __args__["ctx"])
```

with `__args__` injected as an input. Consequences:

- **No module state survives between calls.** Anything that must persist goes
  through `ctx["state"]` in and `state_patch` out. That makes hooks restartable
  at any boundary, which is what resume and crash recovery rely on.
- **Limits apply per hook call** (the 60 s / 1,000 host calls / 4 MiB message
  defaults, plus the web-specific caps). A 40,000-photo backfill is just many
  small calls; the host loops, not the script.
- **One process per hook call, and that's cheap enough.** `run_starlark`
  launches a disposable `yaffo.starlark_worker` subprocess for every call.
  Hooks run per **page or batch**, never per item, so this happens a few
  hundred times per backfill, not tens of thousands. Measured on a dev checkout
  (2026-10-08): about **21 ms** per evaluator launch, independent of module
  size, and about 0.02 ms per host-call round trip. A 40,000-photo OneDrive
  backfill at 200 records per page is 200 launches (about 4 s). A Google
  Photos export in batches of 50 is 800 launches (about 17 s), against hours of
  upload time. Web calls (100 ms to seconds each) dominate by orders of
  magnitude. Keeping a long-lived evaluator would save little and lose the
  per-call guarantees: kill-on-timeout, fresh limits, and no state leaking
  between calls (the reason `state_patch` is the only way to keep state).
  Re-measure on the frozen PyInstaller build, where `sys.executable` is the
  whole app and startup may be slower. Only if that's well above about 100 ms,
  consider a pooled worker that resets its module between calls.
- Module top level may define constants and helpers only. The static checker
  rejects top-level host calls in adapters.

### Host API profile: `adapter`

Adapters run with a new `adapter` profile in `HOST_API` (alongside `automation`
and `assistant`). It contains **only**:

| function | notes |
|---|---|
| `web_request(connection, method, url, json=None, headers=None, query=None)` | from the web-calls doc; grant-checked, signed by the connection |
| `web_upload_media(connection, method, url, media_item_id, encoding="raw", field=None, fields=None, headers=None)` | targets only; bytes read by the host |
| `url_template(template, **values)` | typed placeholders, so the endpoint detector stays complete |
| `print(...)` | captured into the run's output, as today |

No `data_query`, no Yaffo mutations, no `report_progress` (the engine reports
progress). An adapter's only effect on the world is the web calls it is granted;
its only effect on Yaffo is the data it returns to the host.

`connection` in adapters is always the literal `CONNECTION` (bound by the host
to the binding's actual connection), so one adapter works with any connection
of the right preset.

## Host ↔ Starlark contract

### Module metadata

```python
ADAPTER = {
    "contract": 1,                      # host/adapter contract version
    "role": "source",                   # "source" | "target"
    "provider": "onedrive",             # connection preset this adapter needs
    "title": "OneDrive camera roll",
    "options": {                        # shown as a form on the binding; validated by the host
        "folder": {"type": "string", "default": "special/cameraroll"},
    },
    "hooks": {"list_page": list_page},  # name → top-level def (see Code layout)
    # target-only: "capabilities", see below
}
```

`ADAPTER` is a literal apart from `hooks` (checked statically, like endpoint
URLs), so the UI, the builder, and the consent screen can read it without
running code.

### Contract changes: no old versions, regenerate instead

The host supports **exactly one** contract, the current one. There is no
compatibility layer for older adapters. Rewriting an adapter is LLM time, and
carrying old contracts would be permanent code in an open-source side project.

- **`contract` is an integer, `CURRENT_CONTRACT` in the host.** It's bumped for
  any change an existing adapter could get wrong: hook signatures, `ctx` or
  envelope shape, record or batch-item fields, capabilities, or the *meaning*
  of an unchanged field. Shape changes are mostly caught by validation anyway;
  the number catches semantic changes validation can't see, and gives a clear
  message.
- **Every adapter is validated against the current contract**, on write, on
  publish, and at app start after an upgrade (re-running the static check over
  every custom adapter's published code). Validation means: `ADAPTER["contract"]
  == CURRENT_CONTRACT`, the static rules in *Code layout*, and the metadata
  schema.
- **A failure marks the adapter `invalid`** (`status`, with an error code such
  as `contract_outdated`). Bindings using it pause with `runtime.status =
  "invalid_config"` and run history says why. Nothing runs on an adapter that
  doesn't validate.
- **The fix is regeneration.** The adapter page shows *Regenerate*, which opens
  the builder with the old code, the validation errors, and the current contract
  docs, and asks for a rewrite. The result goes through normal draft → Test →
  publish, including re-authorization if its endpoints change.
- **Presets ship with the app**, so they're always on the current contract.
  Upgrading the app re-seeds them (the `preset_version` rule).

**Stored data is different: it's migrated, not invalidated.** The JSON in
`automations.sync`, `sync_adapters.working`/`published`/`grants`, and
`connections.config` is Yaffo's own data, not generated code. When its schema
actually changes, the change ships with a data migration (a numbered migration
under `yaffo/scripts/db/migrations/`, per [Project Context](project-context.md))
that rewrites the stored JSON to the new shape. Between schema changes the JSON
is simply validated against the current schema on load. Anything that still
fails (a bug, a hand edit) marks that binding `invalid_config` rather than
being guessed at.

### `ctx` (passed to every hook)

```python
ctx = {
    "options": {...},          # the binding's validated options
    "state": {...},            # runtime.state + "checkpoint" (read-only)
    "run": {
        "preview": False,      # True in Test
        "run_id": 812,         # the Job id
        "pass": "incremental", # "backfill" | "incremental" | "reconcile"
    },
}
```

### Common return envelope

Every hook returns a dict. Keys the host doesn't recognize are an error, not
ignored, so a typo can't silently drop data.

```python
{
    "state_patch": {"key": value, "other": None},  # optional; None deletes a key
    "error": None,                                 # or an error dict (hook-level failure)
    ...hook-specific keys...
}
```

**Errors are data** (as for host calls today). Error dicts carry a **code**,
and the UI translates it at render time (see the job status-codes convention):

```python
{"code": "rate_limited", "retry_after_s": 120, "message": "Graph 429"}
```

| code | host behavior |
|---|---|
| `rate_limited` | stop this run, keep the cursor, reschedule after `retry_after_s` |
| `retry_later` | transient (5xx, timeout): retry with backoff, then fail the item or page |
| `auth` | stop, mark the connection "needs sign-in", don't retry |
| `not_found` | targets: the remote item or album is gone → reconcile; sources: skip |
| `rejected` | permanent per-item failure (bad format, too large); recorded, not retried |
| `fatal` | stop the run; message goes to run history |

Hooks can return an error for the whole call or per item (in `results`).

### Source hooks

```python
def list_page(cursor, ctx):
    """One page of records. `cursor` is None on the first call of a pass; the host
    then passes back whatever `next_cursor` this hook returned."""
    return {
        "records": [record, ...],
        "next_cursor": "...",   # None = this pass is complete
        "checkpoint": "...",    # optional; stored as the start point of the next
                                # incremental pass, committed only when the pass completes
        "state_patch": {...},
    }

def resolve_download(record, ctx):
    """Optional. For providers whose download URLs expire (or need a call to get),
    the host calls this right before downloading instead of using record["download"]."""
    return {"download": {"url": "...", "headers": {...}}}
```

**Cursor semantics.** `cursor=None` with `ctx.run.pass == "incremental"` means
"start from the stored checkpoint" (the adapter reads it from
`ctx["state"]["checkpoint"]`, which the host maintains). The host persists
`next_cursor` after **every** page, so an interrupted backfill resumes mid-pass.
The `checkpoint` is promoted only after the final page, so a half-finished pass
never advances the delta point.

**Record schema** (validated by the host; invalid records fail the page):

```python
{
    "source_id": "A1B2C3",                  # required; stable provider id
    "filename": "IMG_1234.HEIC",            # required; sanitized by the host
    "download": {"url": "https://...", "headers": {}},  # or omit and implement resolve_download
    "size": 2311042,                        # optional; enables pre-download caps
    "mime": "image/heic",                   # optional; the host still sniffs the bytes
    "hash": {"algo": "sha1", "value": "…"}, # optional; enables dedupe before download
    "taken_at": {                           # optional hints; file EXIF wins when present
        "local": "2026-07-04T18:22:10",     #   naive wall clock, matches media_items.date_taken
        "utc": "2026-07-04T23:22:10Z",      #   only converted if `offset` is known
        "offset": "-05:00",
    },
    "location": {"lat": 41.88, "lon": -87.63},  # optional hint; EXIF wins
    "deleted": False,                       # tombstone from a delta feed
    "updated_at": "2026-07-05T08:00:00Z",   # optional; lets the host skip unchanged records
}
```

`download.url` must be on the connection's `base_urls` or `followup_hosts`;
the host enforces this the same way it does for `web_request`.

**Imports are files only.** Records carry what's needed to fetch, dedupe, and
date/place a file, but no provider-side organization: no albums and no
captions. Only about five of the providers considered have both concepts
(Google via Takeout, OneDrive, Immich, Flickr, Apple), each means something
slightly different, captions are rarely filled in outside Flickr, and Yaffo has
no description field to put them in. Anything a camera or editor wrote *into*
the file (EXIF/XMP description, keywords) survives, because the file is
imported untouched. Optional `albums`/`caption` fields could be added later
as a contract change (see *Contract changes*). Exports are different:
`target` and `caption` stay, because they come from Yaffo's own data.

### Target hooks

```python
def push(batch, ctx):
    """Send a batch of items. The host has already diffed, chunked to
    ADAPTER.max_batch, and written an intent row for each item."""
    return {
        "results": [
            {"media_item_id": 41, "remote_id": "AF1Qip…"},
            {"media_item_id": 42, "error": {"code": "rejected", "message": "unsupported format"}},
        ],
        "state_patch": {"albums": {"For Mom": "ALB123"}},
    }

def remove(items, ctx):
    """Optional (capability `remove`). items: [{media_item_id, remote_id, target}]."""
    return {"results": [{"media_item_id": 41}], "state_patch": {...}}

def update(items, ctx):
    """Optional (capability `update`). Caption/target changes for items already pushed.
    items: [{media_item_id, remote_id, target, caption, previous: {target, caption}}]."""
    return {"results": [...]}

def list_remote(cursor, ctx):
    """Optional (capability `reconcile`). What the remote holds, for crash recovery
    and drift detection. Must only report items this integration can see
    (e.g. Google's app-created items)."""
    return {
        "items": [{"remote_id": "AF1Qip…", "target": "For Mom",
                   "filename": "IMG_1234.jpg", "taken_at": {...}, "hash": None}],
        "next_cursor": None,
    }
```

**Batch item** (what `push` receives):

```python
{
    "media_item_id": 41,
    "target": "For Mom",          # routing key from the selection
    "caption": "Mom and Ava at the lake",
    "filename": "IMG_1234.jpg",
    "mime": "image/jpeg",
    "size": 2311042,
    "taken_at": {"local": "2026-07-04T18:22:10"},
}
```

The adapter uploads with `web_upload_media(CONNECTION, …, item["media_item_id"])`.
It never has the bytes or the path.

**Target capabilities** in `ADAPTER`:

```python
"capabilities": {
    "max_batch": 50,          # host chunks to this
    "remove": True,           # implements remove()
    "update": False,          # implements update(); otherwise caption changes are ignored
    "reconcile": True,        # implements list_remote()
    "targets": "many",        # "one" (ignores routing) | "many" (routes by target key)
    "kind": "collection",     # "collection" (album/folder; items persist) |
                              # "stream" (chat/email/webhook; send-only, no remove)
}
```

The engine adapts its behavior to these: for `stream` targets the diff is only
"not yet sent". When a selection change implies removals the target can't do,
the run reports them ("3 photos left the selection but can't be removed from
Telegram") instead of silently dropping them.

### Selector script (target selection `script`)

Runs in the existing read-only `automation` profile (`data_query`,
`face_similarity`, `match_people`; no mutations, no web). Its trailing
expression is the desired set:

```python
moms = data_query({"source": "people", "name": {"eq": "Mom"}})
...
[{"media_item_id": p["id"], "target": "For Mom", "caption": caption_for(p)} for p in picks]
```

The host validates it (ids exist, captions are strings within a length cap,
targets are non-empty strings) and caps it (desired-set size limit, initially
5,000 to match `data_query`).

## Triggers and the `album_changed` event

Bindings use the ordinary automation triggers: a schedule (`AutomationTrigger`
with `cron`) or events. Sources are schedule-driven: there's nothing local to
react to. Targets whose selection is an **album** should also react when the
album changes, so a photo added to *For Mom* appears for Mom within a minute,
not at the next scheduled run. This needs one new domain event.

### The event

Add `EVENT_ALBUM_CHANGED = "album_changed"` to the fixed catalog in `models.py`
(`EVENTS`, label "Album changed"). It's a general event, not sync-specific: any
automation can subscribe, e.g. a custom script that tags every photo added to
*Portfolio*.

Payload, and the matching new `EventContext` field:

```python
emit_event(EVENT_ALBUM_CHANGED, {
    "album_id": 7,
    "change": "added",              # added | removed | renamed | deleted
    "media_item_ids": [41, 42, 43], # the photos added/removed; [] for renamed/deleted
})

@dataclass(frozen=True)
class EventContext:
    ...
    album_id: int | None = None     # new; set only by album_changed
    change: str | None = None       # new; set only by album_changed
```

Both reach Starlark as `ctx["album_id"]` / `ctx["change"]`, through the same
`dispatch_event_task` → `invoke_automation` → `executor.context_globals` path
as `groups`.

**Not emitted for** reorder or cover changes: they don't change what an album
contains, and nothing downstream needs them. If a later consumer does, add
`reordered` as another `change` value rather than a new event.

### Where it's emitted

Album membership changes in two places today, both after their commit, like
`media_modified`:

| call site | `change` |
|---|---|
| `routes/albums.py`: `items/add` (`add_items`, and `add_matching` for "add all matching filter") | `added` |
| `routes/albums.py`: `items/remove` (`remove_items`, `remove_all`) | `removed` |
| `routes/albums.py`: `details` (`update_album`), only when the name changes | `renamed` |
| `routes/albums.py`: `delete` | `deleted` |
| host actions `add_to_album` / `remove_from_album` / `update_album` / `delete_album` (`automation_actions.py`), used by automations and assistant plans | same as above |

Emitting from the host actions goes through `emit_event`, so the event carries
the run's **causal chain** and the existing loop guard applies. An automation
that adds to album A when album A changes fires once per chain, not forever.
**Granularity is one event per operation**, not per photo. "Add all 3,000
matching" is one `added` event. For bulk operations `media_item_ids` is capped
(5,000, the `data_query` cap) and the payload sets `"truncated": true` beyond
that. Subscribers that need exactness re-read the album, which is what the sync
engine does anyway (it diffs the whole album, not the event's ids).

### Per-trigger filter: `album_id`

`dispatch_event_task` today matches triggers on `event_type` only, so every
`album_changed` would wake every subscriber. Add an optional filter in the
trigger's existing `config` JSON:

```python
AutomationTrigger(trigger_type="event", event_type="album_changed",
                  config={"album_id": 7, "changes": ["added", "removed"]})
```

`dispatch_event_task` skips a trigger whose `config.album_id` is set and doesn't
match the event's, or whose `config.changes` doesn't include the event's
`change`. Filtering happens in Python after the existing query. Triggers are
few, so there's no need to index into the JSON. This is the first event filter
the dispatcher honors. It's written as a small per-event matcher function so
later filters (e.g. `media_labeled` by label) follow the same shape.

### Sync bindings subscribe automatically

When a target binding's selection is `album`, the binding owns an event trigger
`album_changed` with `config={"album_id": <selected album>}`. It's created with
the binding and updated in the same transaction when the selected album changes
(which also re-asks consent). The user can still add a schedule as a safety net;
the default is both: the event for promptness, a nightly schedule to catch
anything missed (e.g. a run that was paused for sign-in).

Query and script selections can't use `album_changed`. They can subscribe to
the events their rule depends on (`media_indexed`, `media_modified`,
`media_labeled`), or just use a schedule.

### Coalescing bursts

A user curating an album clicks *add* many times in a few minutes. One sync run
per click would mean many small runs racing the per-binding run lock. Instead,
for sync bindings, an event doesn't start a run directly:

1. Dispatch doesn't invoke the binding. It sets the event trigger's existing
   `next_run_at` column to now + the **settle window** (default 60 s,
   `config.settle_seconds` on the trigger), and only if `next_run_at` is
   currently NULL. That's one conditional `UPDATE`, so concurrent events
   can't race.
2. The schedule dispatcher's tick (the existing poll path, which already fires on
   `next_run_at <= now`) also picks up **event** triggers with a due
   `next_run_at`. It clears the column and starts the run.
3. A change that arrives while a run is in progress sets `next_run_at` again,
   so the next tick after that run finishes picks it up.

This keeps the deferral off `automations.sync`, whose `runtime` part has a
single writer (the engine), and reuses dispatcher bookkeeping that already
exists. Only sync bindings opt in, via `config.settle_seconds`; other
`album_changed` subscribers are dispatched immediately, like every event today.

The run diffs the whole album, so it doesn't matter how many events were
coalesced or what ids they carried. A deferred run starts with an empty causal
chain (it's started by the tick, not the event). That's safe for targets,
which never write Yaffo albums. If a tick finds the binding's previous run
still holding the run lock, it leaves `next_run_at` set and tries again on the
next tick. `deleted` skips the settle window: the
binding pauses with `selection_missing` right away (see *What we give up by
using JSON*).

### Interaction with the loop guard

Sync bindings never write Yaffo albums: a target writes only to the remote
provider, and a source imports files only. So a sync can't re-trigger itself
through `album_changed`. A custom script can still create a deliberate chain
(e.g. "add newly imported OneDrive photos of Mom to *For Mom*", which then
wakes the Google Photos target). The causal chain records both automations, so
if a script closes the loop (target change → script → album), the guard breaks
it as it does today.

## Host engine

### Source run

```
pass = backfill (no checkpoint yet) | incremental
cursor = stored next_cursor (resume) or None
loop:
    page = call list_page(cursor, ctx)            # fresh evaluator
    on page.error → handle by code; stop
    validate records                              # schema, URL hosts, sizes
    for each record (bounded concurrency):
        skip if sync_items has (binding, source_id) with same updated_at
        skip + link if hash matches an existing media_items.sha1  (cross-library dedupe)
        tombstone → mark the sync_items row deleted_at_source (never delete the local file; see Source deletions)
        resolve_download if needed → download to temp in the import root
        sniff type (images/videos only), enforce size caps, check free space
        atomic rename to Imports/<binding>/<YYYY>/<MM>/<sanitized name>
        record watcher suppression (Mechanism 2 in Automations) so file_sync doesn't double-index
    index the page's new files (existing indexer), passing taken_at/location hints
    commit sync_items + runtime (state_patch, next_cursor) in one transaction (chunked)
    cursor = page.next_cursor; until None
promote checkpoint
```

Pages are committed in chunks (lock-free compute, then a bulk flush), per the
SQLite concurrency rules. Long backfills run as a resumable task-queue job; a
run's time budget ends the loop cleanly at a page boundary, and the next run
continues from the stored cursor.

### Target run

```
desired = selection()                            # [{media_item_id, target, caption}]
current = sync_items for this binding
to_push   = desired − current (by media_item_id, target)
to_remove = current − desired                    (if capability remove)
to_update = caption/target changed               (if capability update)
for each chunk of to_push:
    write intent rows (status = pending, attempt n)      # commit
    res = call push(chunk, ctx)
    commit results (status = sent | failed[code]) + state_patch in one transaction
same for remove / update
```

**Crash recovery.** If a run dies after `push` sent data but before results
commit, the next run finds `pending` intent rows. With `reconcile` capability
the host calls `list_remote`, matches pending rows to remote items (by
`remote_id` if known, else `filename` + `taken_at` within the target), and
marks them sent, so nothing is re-uploaded. Without it, the host re-pushes and
the run notes possible duplicates. This replaces the
"crash between `batchCreate` and `state_set`" problem in the web-calls doc with
one tested host path.

A periodic `reconcile` pass (e.g. weekly) also catches drift: items the user
deleted remotely are marked `removed_remotely` and **not** re-pushed unless the
user asks.

### Source deletions

**Yaffo never deletes a local file because the source deleted it.** When a
record arrives with `deleted: True`, the engine only sets the item's
`sync_items` status to `deleted_at_source`. The photo, its file, faces, tags,
labels, and album membership are untouched. Reasons:

- The library on the SD card is the master copy; providers are inputs to it.
  A remote deletion is often cleanup of cloud storage (freeing quota, removing
  a phone backup), not "I don't want this photo any more".
- Deletions can be wrong or partial: a reset delta token, a provider bug, or a
  user emptying the wrong folder. A sync that deletes would turn those into data
  loss in the one place that's supposed to be safe.
- It keeps the source side to one effect: files only ever appear in the library.

What the user gets instead:

- A "Deleted at source" filter (from `sync_items.status`), so they can
  review those photos and delete them in Yaffo with the normal delete
  (which sends files to the OS trash) if they want to.
- A count on the binding's run summary ("12 photos deleted in OneDrive; kept in
  your library").

A tombstone for an item Yaffo never imported is recorded as `deleted_at_source`
with no `media_item_id`, so it's skipped if the provider later lists it again.
An item that reappears at the source after a deletion is set back to `imported`
(the local file was never removed, so nothing is downloaded).

### Validation of adapter output

The host treats adapter output as untrusted input:

- unknown keys, wrong types, missing required fields → page or call fails with
  `fatal` (the adapter is buggy; don't half-apply it);
- download URLs and follow-ups restricted to connection hosts;
- `results` must reference only `media_item_id`s from the batch it was given;
- `state_patch` size-capped (64 KiB per binding, JSON only).

## Test / preview

| | Source | Target |
|---|---|---|
| Web reads | **live** for the first page (`GET`s and POST-as-read endpoints the adapter has granted, marked `read` risk) | live `list_remote` first page, if granted |
| Web writes | n/a | recorded, not sent (`$ref:N` tokens for returned ids, as today) |
| Shows | the first page as a table of records (filename, taken, size, hash, "already in library?") plus validation errors | the diff ("would add 12 to *For Mom*, remove 3"), then the adapter's recorded web calls per batch |
| Library writes | none, no downloads | none |

Live read GETs in Test answer the web-calls doc's open question **for
adapters**: without them, neither the builder nor the user can see whether a
source adapter's field mapping is right.

## Authorization and consent

Two layers, matching the two separate questions a user is asked:

1. **Adapter grants**: what this code may call. Detected statically from the
   adapter exactly as in the web-calls doc (literal connection, method, URL
   template), approved at publish, re-opened when code widens. **Presets** ship
   with their manifests and a pinned code hash; installing one shows the manifest
   once.
2. **Binding consent**: what the engine will do with it.
   - Source: "Import new photos from *OneDrive › Camera Roll* into
     *Pictures › Imports › OneDrive*." The import root is part of the consent;
     changing it re-asks.
   - Target: "Send *the photos matching this rule* to *Google Photos › For Mom*."
     **Every new version of the selection re-asks**: any change to its kind,
     to a query, to which album is selected, or a newly published selector
     script. There's no attempt to work out whether a change widens or narrows
     the selection. Changes to the *members* of a selected album don't re-ask
     (the album is the user's consent, item by item).
   - Endpoints marked `shares_externally` (OneDrive `createLink`, Immich shared
     links) keep their own separate confirmation, as in the web-calls doc.

## Preset sketches

### OneDrive source (delta feed)

```python
GRAPH = "https://graph.microsoft.com/v1.0"

def list_page(cursor, ctx):
    if cursor:
        url = cursor                                   # nextLink, a URL the host itself returned
    elif ctx["state"].get("checkpoint"):
        url = ctx["state"]["checkpoint"]               # deltaLink from the last full pass
    else:
        url = url_template(GRAPH + "/me/drive/{path}:/delta",
                           path=ctx["options"]["folder"])
    r = web_request(CONNECTION, "GET", url)
    if r["status"] == 429:
        return {"error": {"code": "rate_limited",
                          "retry_after_s": int(r["headers"].get("retry-after", "60"))}}
    if r["status"] != 200:
        return {"error": {"code": "retry_later", "message": "graph %d" % r["status"]}}
    body = r["json"]
    return {
        "records": [to_record(i) for i in body["value"] if "photo" in i or "deleted" in i],
        "next_cursor": body.get("@odata.nextLink"),
        "checkpoint": body.get("@odata.deltaLink"),
    }

def to_record(i):
    if "deleted" in i:
        return {"source_id": i["id"], "filename": i.get("name", ""), "deleted": True}
    taken = i["photo"].get("takenDateTime")
    loc = i.get("location")
    return {
        "source_id": i["id"],
        "filename": i["name"],
        "download": {"url": url_template(GRAPH + "/me/drive/items/{id}/content", id=i["id"])},
        "size": i.get("size"),
        "mime": i.get("file", {}).get("mimeType"),
        "hash": {"algo": "sha1", "value": i["file"]["hashes"]["sha1Hash"]}
                if "sha1Hash" in i.get("file", {}).get("hashes", {}) else None,
        "taken_at": {"utc": taken} if taken else None,
        "location": {"lat": loc["latitude"], "lon": loc["longitude"]} if loc else None,
        "updated_at": i.get("lastModifiedDateTime"),
    }

ADAPTER = {"contract": 1, "role": "source", "provider": "onedrive",
           "title": "OneDrive folder",
           "options": {"folder": {"type": "string", "default": "special/cameraroll"}},
           "hooks": {"list_page": list_page}}
```

`ADAPTER` comes last because Starlark evaluates the module top to bottom: the
`hooks` names must already be defined. `to_record` is a private helper.

Note `nextLink`/`deltaLink` are full URLs returned by a granted call. They pass
the grant check through the follow-up-URL rule (the host recognizes URLs it
returned), so the script doesn't need a literal for them.

### Google Photos target

```python
API = "https://photoslibrary.googleapis.com/v1"

def push(batch, ctx):
    albums = dict(ctx["state"].get("albums", {}))
    results, by_target = [], {}
    for item in batch:
        by_target.setdefault(item["target"], []).append(item)
    for target, items in by_target.items():
        if target not in albums:
            r = web_request(CONNECTION, "POST", API + "/albums", json={"album": {"title": target}})
            if r["status"] != 200:
                return {"error": {"code": "retry_later"}, "state_patch": {"albums": albums}}
            albums[target] = r["json"]["id"]
        new_items, pending = [], []
        for item in items:
            up = web_upload_media(CONNECTION, "POST", API + "/uploads", item["media_item_id"],
                                  headers={"X-Goog-Upload-Protocol": "raw"})
            if up["status"] != 200:
                results.append({"media_item_id": item["media_item_id"],
                                "error": {"code": "retry_later"}})
                continue
            new_items.append({"description": item["caption"] or "",
                              "simpleMediaItem": {"uploadToken": up["text"],
                                                  "fileName": item["filename"]}})
            pending.append(item["media_item_id"])
        r = web_request(CONNECTION, "POST", API + "/mediaItems:batchCreate",
                        json={"albumId": albums[target], "newMediaItems": new_items})
        for mid, res in zip(pending, r["json"].get("newMediaItemResults", [])):
            if "mediaItem" in res:
                results.append({"media_item_id": mid, "remote_id": res["mediaItem"]["id"]})
            else:
                results.append({"media_item_id": mid, "error": {"code": "rejected",
                                "message": res.get("status", {}).get("message", "")}})
    return {"results": results, "state_patch": {"albums": albums}}

def remove(items, ctx):
    ...  # albums/{id}:batchRemoveMediaItems, grouped by target

def list_remote(cursor, ctx):
    ...  # mediaItems:search {albumId, pageToken}; needs photoslibrary.readonly.appcreateddata

ADAPTER = {"contract": 1, "role": "target", "provider": "google_photos",
           "title": "Google Photos album",
           "capabilities": {"max_batch": 50, "remove": True, "update": False,
                            "reconcile": True, "targets": "many", "kind": "collection"},
           "hooks": {"push": push, "remove": remove, "list_remote": list_remote}}
```

### Telegram target (stream)

```python
def push(batch, ctx):
    results = []
    for item in batch:
        r = web_upload_media(CONNECTION, "POST",
                             "https://api.telegram.org/bot{token}/sendPhoto",
                             item["media_item_id"], encoding="multipart", field="photo",
                             fields={"chat_id": ctx["options"]["chat_id"],
                                     "caption": item["caption"] or ""})
        ok = r["status"] == 200 and r["json"]["ok"]
        results.append({"media_item_id": item["media_item_id"],
                        "remote_id": str(r["json"]["result"]["message_id"]) if ok else None,
                        "error": None if ok else {"code": "retry_later"}})
    return {"results": results}

ADAPTER = {"contract": 1, "role": "target", "provider": "telegram_bot",
           "title": "Telegram chat",
           "options": {"chat_id": {"type": "string"}},
           "capabilities": {"max_batch": 10, "remove": False, "update": False,
                            "reconcile": False, "targets": "one", "kind": "stream"},
           "hooks": {"push": push}}
```

`{token}` is a **connection placeholder** filled in by the host from the secret
(Telegram puts the bot token in the path). The script never sees its value,
and the detector treats it as part of the connection, not a script value.

## Sources that aren't adapters

Some imports have no listing API but can still feed the same engine as
**host-native record producers** (Python, reviewed, one per kind). They emit the
same record schema, so dedupe, file writes, metadata merge, and indexing are
shared:

| producer | why it isn't an adapter |
|---|---|
| Google Photos Picker | needs a person in the loop (a picker session in their browser); a utility button, not a schedule |
| Google Takeout ingester | a local ZIP with JSON sidecars; no network |
| macOS Photos (osxphotos / PhotoKit) | local bridge with macOS permissions, not web calls |

## Data model

**Rule: configuration and per-binding state are JSON; the per-item ledger is a
table.** Bindings, grants, adapter state, and cursors are always read and written
whole, one binding at a time, by the engine. Nothing queries *across* them
except a few small admin lookups. They are validated in code (named
dataclasses plus `jsonschema`, which Yaffo already uses for `data_query`) at
every write and at load. Item rows are different: there can be tens of
thousands per binding, the engine looks them up by key and status on every
page, they join to `media_items`, and they must follow media deletes. That
needs a table.

So this design adds **three tables, three columns**:

```
 connections ◄─────────── automations.sync.connection_id        (JSON reference)
 sync_adapters ◄───────── automations.sync.adapter_id           (JSON reference)
   (code, metadata, manifest, grants: JSON)
 automations
   .sync  (JSON: binding config, consent, state, cursors)
   .working_code / .published_code  (selector script, existing columns)
     └──< sync_items ──► media_items
 conversations.sync_adapter_id ──► sync_adapters   (builder chat for a custom adapter)
 media_items.sha1                                  (dedupe)
```

`connections` is shared with [Automation Web Calls](automation-web-calls.md#connection).
All changes follow the migration rules in [Project Context](project-context.md).

### `connections` (table)

A table rather than JSON because a connection is a shared entity: several
bindings and the web host functions refer to it by id, its `slug` must be
unique, and its `status` is updated by token refresh independently of any
binding.

```python
class Connection(db.Model):
    """A named, user-created credential for one remote service. Secrets are not
    stored here: `secret_ref` names the OS keychain entry (keyring) holding the
    token/key. Scripts only ever see the literal CONNECTION, bound by the host."""

    __tablename__ = "connections"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String, unique=True, nullable=False)       # "google_photos", "moms_onedrive"
    name = db.Column(db.String, nullable=False)
    provider = db.Column(db.String, nullable=False)                # preset key: "onedrive", "immich", ...
    config = db.Column(db.JSON, nullable=False)                    # ConnectionConfig: auth kind/endpoints/
                                                                   # scopes, base_urls, followup_hosts
    secret_ref = db.Column(db.String, nullable=True)               # keychain entry; NULL until signed in
    status = db.Column(db.String, nullable=False)                  # ready | needs_sign_in | disabled
    created_at = db.Column(db.DateTime, default=utcnow)
    last_used_at = db.Column(db.DateTime, nullable=True)
```

### `sync_adapters` (table, mostly JSON)

A table because adapters are shared: one preset serves many bindings, and a
custom adapter has its own draft/publish lifecycle and builder chat. Everything
derived from the code is JSON.

```python
class SyncAdapter(db.Model):
    """One adapter module (see Code layout). Presets are seeded from
    yaffo/sync/presets/*.star and are read-only in the UI, but run through the
    same path as custom adapters. `working` / `published` are AdapterVersion JSON
    blobs; the code itself stays in Text columns so diffs and the builder treat
    it like automation code."""

    __tablename__ = "sync_adapters"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String, unique=True, nullable=False)       # "preset.onedrive_source", "custom.flickr_set"
    name = db.Column(db.String, nullable=False)
    role = db.Column(db.String, nullable=False)                    # source | target
    provider = db.Column(db.String, nullable=False)                # must match the connection's provider
    is_preset = db.Column(db.Boolean, nullable=False, default=False)
    preset_version = db.Column(db.String, nullable=True)           # bump → re-seed on app start

    working_code = db.Column(db.Text, nullable=True)
    published_code = db.Column(db.Text, nullable=True)
    working = db.Column(db.JSON, nullable=True)                    # AdapterVersion for working_code
    published = db.Column(db.JSON, nullable=True)                  # AdapterVersion for published_code
    grants = db.Column(db.JSON, nullable=False, default=list)      # [WebGrant]

    status = db.Column(db.String, nullable=False)                  # draft | ready | invalid
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
```

### `automations.sync` (new JSON column)

A binding *is* its automation, so its configuration goes on the `automations`
row rather than a 1:1 table. Dispatch routes an automation with a non-NULL
`sync` to the sync engine (a third route in `automation_dispatch.py`, next to
system handlers and custom code). The existing `working_code` /
`published_code` hold the selector script when `selection.kind == "script"`.

The column holds two parts with different writers, which the engine keeps
apart:

- `binding`: written only by the UI/builder (and re-consent).
- `runtime`: written only by the engine, in the same transaction as the page or
  batch of `sync_items` it describes.

### JSON shapes (validated in code)

Named frozen dataclasses in `yaffo/sync/schemas.py`, each with `from_dict` that
runs a `jsonschema` validator and `to_dict` for storage. Loading invalid JSON
raises, and the engine pauses the binding (`status: "invalid_config"`) rather
than running on a guess.

```python
@dataclass(frozen=True)
class ConnectionConfig:
    auth_kind: str                    # oauth2 | oauth1a | api_key | sigv4
    auth: dict                        # endpoints, scopes, key header, region (by auth_kind)
    base_urls: list[str]
    followup_hosts: list[str]

@dataclass(frozen=True)
class AdapterVersion:                 # sync_adapters.working / .published
    code_hash: str                    # sha256 of the matching code column
    contract: int
    metadata: dict                    # the ADAPTER dict, extracted statically; hooks as names
    manifest: list[dict]              # [{method, url_pattern, grant_kind, risk, line}]

@dataclass(frozen=True)
class WebGrant:                       # sync_adapters.grants[]
    method: str
    url_pattern: str                  # typed placeholders: {id} {filename} {path}
    grant_kind: str                   # endpoint | followup
    followup_of: str | None           # "METHOD url_pattern" of the granting endpoint
    risk: str                         # read | write | shares_externally
    granted_code_hash: str            # must equal published.code_hash at call time
    granted_at: str

@dataclass(frozen=True)
class Selection:
    kind: str                         # album | query | script
    album_id: int | None = None
    query: dict | None = None         # a data_query dict, validated by data_query's own schema

@dataclass(frozen=True)
class SyncBinding:                    # automations.sync["binding"]
    role: str                         # source | target
    adapter_id: int
    connection_id: int
    options: dict                     # validated against the adapter's ADAPTER["options"]
    import_root: dict | None          # sources: {"media_dir_id": uuid, "subpath": "Imports/OneDrive"}
    selection: Selection | None       # targets
    consent_hash: str | None          # hash of everything the user approved (see below)
    consented_at: str | None

@dataclass(frozen=True)
class SyncCursor:
    pass_kind: str | None             # backfill | incremental
    next_cursor: str | None           # NULL = no pass in progress
    checkpoint: str | None            # start of the next incremental pass
    pass_started_at: str | None
    last_completed_at: str | None

@dataclass(frozen=True)
class SyncRuntime:                    # automations.sync["runtime"]
    status: str                       # ready | needs_consent | needs_sign_in | paused | invalid_config
    state: dict                       # the adapter's state_patch store; 64 KiB cap
    cursors: dict[str, SyncCursor]    # "list" (sources) | "reconcile" (targets with list_remote)
```

Stored example (a OneDrive source):

```json
{
  "binding": {
    "role": "source", "adapter_id": 3, "connection_id": 1,
    "options": {"folder": "special/cameraroll"},
    "import_root": {"media_dir_id": "6f1c…", "subpath": "Imports/OneDrive"},
    "selection": null,
    "consent_hash": "9b2e…", "consented_at": "2026-10-08T17:02:11Z"
  },
  "runtime": {
    "status": "ready",
    "state": {},
    "cursors": {"list": {"pass_kind": "incremental", "next_cursor": null,
                         "checkpoint": "https://graph.microsoft.com/v1.0/me/drive/…delta?token=…",
                         "pass_started_at": null, "last_completed_at": "2026-10-08T06:00:41Z"}}
  }
}
```

The adapter sees `runtime.state` merged with `{"checkpoint": …}` as
`ctx["state"]`. Cursors aren't in `state`, so a `state_patch` can't overwrite
them.

### `sync_items` (table)

One table for both directions. The fields differ only a little, so a
role-specific `item_key` plus a small JSON `details` column replaces separate
source and target tables.

```python
class SyncItem(db.Model):
    """One item a sync binding has seen (source) or sent (target). Unique on
    (automation, item_key), which is what makes re-listing and re-pushing
    idempotent. For targets the row doubles as the intent log: written 'pending'
    before push(), resolved after; rows left pending by a crashed run are
    reconciled on the next run.

    item_key: sources → the provider's source_id;
              targets → f"{media_item_id}:{target}".
    media_item_id goes NULL if the local photo is deleted. For a source that must
    not cause a re-import; for a target the row has left the selection (remove,
    if the adapter supports it)."""

    __tablename__ = "sync_items"

    id = db.Column(db.Integer, primary_key=True)
    automation_id = db.Column(db.Integer, db.ForeignKey("automations.id", ondelete="CASCADE"), nullable=False)
    item_key = db.Column(db.String, nullable=False)
    media_item_id = db.Column(db.Integer, db.ForeignKey("media_items.id", ondelete="SET NULL"), nullable=True)
    remote_id = db.Column(db.String, nullable=True)                # target: provider id (sources: = item_key)
    target = db.Column(db.String, nullable=True)                   # target routing key; NULL for sources
    fingerprint = db.Column(db.String, nullable=True)              # source: record.updated_at;
                                                                   # target: hash of caption → update()
    status = db.Column(db.String, nullable=False)                  # source: imported | linked_existing | skipped |
                                                                   #   failed | deleted_at_source
                                                                   # target: pending | sent | failed | removing |
                                                                   #   removed | removed_remotely | cant_remove
    error_code = db.Column(db.String, nullable=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    run_id = db.Column(db.String, db.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    details = db.Column(db.JSON, nullable=True)                    # source: {relative_path, sha1};
                                                                   # never queried
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("automation_id", "item_key"),
        Index("ix_sync_items_status", "automation_id", "status"),
        Index("ix_sync_items_media_item", "media_item_id"),
    )
```

Why this stays a table:

- **Lookups per page.** "Have I seen `source_id` X?" and "which rows are
  `pending`?" for one binding, thousands of times per backfill. Keys and indexes
  make that cheap; a JSON blob would be parsed and rewritten in full per page.
- **Write locks.** The engine commits a page's items in chunks (lock-free
  compute, then bulk flush). Rewriting one growing JSON value per page would
  hold the SQLite write lock longer and longer as the backfill proceeds.
- **Foreign keys.** `media_item_id ON DELETE SET NULL` keeps the ledger correct
  when a photo is deleted, with no code path to forget.
- **The UI does query it**: per-photo "sent to *For Mom*" badges, a "failed
  items" list on the run page, "imported from OneDrive" filters.

Status values are stored as codes and translated at render time, like Job
outcome codes.

### Changes to existing tables

| table | change | why |
|---|---|---|
| `automations` | add `sync` (JSON, nullable) | binding config and runtime state (above) |
| `media_items` | add `sha1` (String(40), nullable, indexed) | cross-library dedupe on import; match Immich/OneDrive hashes before downloading. Computed at index time; existing rows backfilled by a maintenance task |
| `conversations` | add `sync_adapter_id` (FK → `sync_adapters`, CASCADE, nullable) | the builder chat for a custom adapter, like `automation_id` for automations |

### What we give up by using JSON, and how code covers it

| lost | handled by |
|---|---|
| FK `RESTRICT` on deleting a connection or adapter in use | the delete route checks for bindings first. Automations are few, so a scan of `automations.sync` (or SQLite `json_extract`) is fine. The UI lists the bindings that block the delete |
| FK `SET NULL` on deleting the selected album | the engine checks the album exists at run start; if not, the binding pauses (`paused`, code `selection_missing`) rather than treating "no album" as "remove everything remotely" |
| Column types/constraints | `jsonschema` on every write and load, plus a drift-guard test that each dataclass's fields match its schema |
| Partial updates | `binding` and `runtime` are always replaced whole by their single writer; one run per binding at a time (the engine takes a per-automation run lock), so there are no concurrent writers to one blob |
| Schema evolution | JSON shapes carry no version of their own; a shape change ships with a data migration that rewrites `automations.sync`, like any other migration |

### Consent hash

`binding.consent_hash` is a hash over: role, adapter id and
`published.code_hash`, connection id, `import_root`, and selection (kind plus
album id, query, or the selector's published code hash). The engine recomputes
it at run start; a mismatch sets `runtime.status = "needs_consent"` and the run
stops before any web call. Because the selection's query and the selector's
code hash are part of the hash, every new version of the selection needs
re-authorization by construction. The UI asks at publish time, so this
check at run start is only a backstop.

### Lifecycle rules

- **Delete a sync automation**: cascades its `sync_items` (the config goes with
  the row). Nothing is removed remotely and no imported file is deleted.
- **Delete a connection or adapter**: refused while bindings reference it.
  Presets can't be deleted.
- **Republish a custom adapter**: new `published.code_hash`. Grants whose hash
  no longer matches stop authorizing calls. If the new manifest only narrows,
  grants are re-stamped automatically; otherwise the publish modal asks.
  Bindings using it get `needs_consent` only if the grant set changed.
- **Preset upgrade** (`preset_version` bump on app update): re-seeded code goes
  through the same rule. A widened manifest pauses the affected bindings with
  `needs_consent` instead of silently granting.

## Compared with the web-calls design

| | web-calls (script does everything) | sync adapters (this doc) |
|---|---|---|
| Provider code | regenerated per user, per automation | one adapter per provider; presets shared |
| Diff, state, retries, crash recovery | in each script | host, tested once |
| Large backfills | don't fit the per-run caps | host-looped pages, resumable |
| Import | needs a script that writes files | host writes; adapter only lists |
| Custom logic | anything | selection (query/script) + custom adapters |
| New runtime machinery | web host functions, grants | same, **plus** hook invocation, the sync engine, and an `adapter` profile |
| Escape hatch | n/a | fully custom web-calling automations (the web-calls doc) remain possible for odd cases |
