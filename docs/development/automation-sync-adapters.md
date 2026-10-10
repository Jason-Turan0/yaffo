# Sync Adapters — Sources and Targets over a Host Sync Engine (Design Sketch)

**Status: proposed, not built. Alternative to [Automation Web Calls](automation-web-calls.md).**
That doc has a custom automation do *everything* for an export (select, diff,
upload, batch, record state) in one Starlark script. This sketch keeps its
primitives (connections, stored as application settings and shared by syncs;
endpoint grants,
`web_request`, `web_upload_media`, follow-up hosts, the static endpoint
detector) and changes who owns what:

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
                 ┌──────────────── Connection (account at a provider: auth, base_urls, followup_hosts)
                 │
 Source sync     ▼                         Host sync engine                      Library
 ┌─────────────────────┐  list_page()   ┌──────────────────────────────┐  write  ┌──────────┐
 │ Source adapter      │ ─────────────► │ validate records             │ ──────► │ Imports/ │
 │ (preset or custom)  │ ◄───────────── │ dedupe (state map + sha1)    │         │  <src>/  │
 └─────────────────────┘  cursor        │ download → sniff → atomic mv │         └────┬─────┘
                                        │ metadata hints → indexer     │              │ index
                                        └──────────────────────────────┘              ▼
                                                                                 media_items
 Target sync
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

A **sync automation** is an automation with a `sync` configuration (a JSON column on
`automations`; see *Data model*). It reuses everything
automations already have: triggers (schedule, events), runs recorded as `Job`s,
run history, enable/disable, and Test. Album-selected targets also react to a
new `album_changed` event (see *Triggers and the `album_changed` event*).

## Concepts

### Object model

One meaning per word. In this doc **provider** always means an external
service as Yaffo knows it; the AI models behind the builder are **LLM
providers**, written out in full. The UI says **service** where this doc says
*adapter* (see *UI*), since that's what a user picks: "Google Photos album",
"Flickr sets · custom".

| object | what it is | where it lives | examples |
|---|---|---|---|
| **Provider** | an external service: how to sign in, which hosts may be called, default scopes. No sync logic | preset: host code; custom: the `connection` block of a custom adapter | `onedrive`, `google_photos`, `immich` |
| **Adapter** | the code for **one direction** against **one provider**: hooks, options, capabilities | a `sync_adapters` row (preset or custom) | "OneDrive folder" (source), "OneDrive folder" (target), "Google Photos album" (target) |
| **Sync automation** (a *sync*) | one running use of an adapter: which account, which options, what goes where, when, and its progress | an `automations` row with a non-NULL `sync` JSON column | "OneDrive camera roll", "For Mom → Google" |
| **Connection** | a signed-in account at a provider, shared by every sync (and builder test) that uses that account | one `application_settings` row per account; the token in the keychain | jason@outlook.com on OneDrive |
| **Selection** (targets) | what leaves Yaffo: an album, a filter, or a selector script | inside the sync's JSON; a script in the automation's code columns | album *For Mom* |
| **Import root** (sources) | where imported files land | inside the sync's JSON | `Pictures/Imports/OneDrive` |
| **Sync items** | the per-item record of what was imported or sent | `sync_items` rows | IMG_1234 → remote id AF1Q… |
| **App credentials** (custom OAuth adapters) | the user's own developer-app registration with the service (client id + secret); identifies the *app*, not an account | keychain, referenced by `sync_adapters.app_secret_ref` | a Flickr non-commercial API key |

```
Provider ──1:many──► Adapter ──1:many──► Sync automation ──1:many──► sync_items
 (onedrive)           (role: source        │  uses 1 Connection   (an account at the adapter's provider; shared)
                       or target)          │  owns 1 Selection    (targets) or 1 Import root (sources)
                                           │  has many Triggers   (existing automation_triggers)
                                           └  has many Runs       (existing Jobs)
```

- **A provider has at most one preset adapter per direction**, plus any
  custom adapters. OneDrive has a source and a target; Google Photos has only
  a target (importing from it goes through the Picker, see *Sources that aren't
  adapters*).
- **An adapter is exactly one direction.** Import from and export to the same
  service are two adapters sharing a provider, never one adapter doing both.
- **An adapter serves many syncs.** Two OneDrive folders imported separately
  are two syncs on the same preset adapter.
- **A sync has exactly one adapter and one connection**, and the connection's
  provider must equal the adapter's. The exception is a built-in source or
  target with no account (the Folder source and target, see *Sources that
  aren't adapters* and *Targets that aren't adapters*), whose syncs have no
  connection.
- **One connection per account, shared.** Whichever sign-in happens first
  creates it (a sync's *Account* step, or the builder's *Test account* tab);
  later syncs on the same provider default to it. It's deleted, and its token
  revoked, when nothing refers to it any more.
- **A custom adapter may point at a test connection**, the account the
  builder's Test (`test_adapter`) uses. It's an ordinary shared connection, so
  the account signed in while building is the one the first sync offers.
- **A custom OAuth adapter owns its app credentials**, entered once while
  building. Every sign-in for its provider runs the OAuth flow with them.
  Presets need none: Yaffo ships their app registration.
- **A custom adapter is its own provider.** It carries a `connection` block, so
  its provider is `custom:<adapter slug>`. A custom Flickr import and a custom
  Flickr export are two adapters declaring two identical providers; that
  duplication is accepted rather than adding a way for one custom adapter to
  borrow another's sign-in setup (see *Building a custom adapter*).

### Provider

The provider key (`"onedrive"`) names a **provider preset** in host code: auth
kind and endpoints, Yaffo's OAuth client id, `base_urls`, `followup_hosts`,
default scopes, and the token revoke endpoint if any. Adapters name the
provider they need in `ADAPTER["provider"]`; custom adapters say `"custom"` and
declare the same fields in their `connection` block.

### Connection

A signed-in account at a provider (as in [Automation Web
Calls](automation-web-calls.md#connection)). The provider supplies how to sign
in and which hosts may be called; the connection holds whose account it is. Adding OAuth 1.0a (Flickr, SmugMug)
and AWS SigV4 (S3, R2, B2) means the connection signs the **whole request**, not
just a header. That happens in the host's request pipeline, so adapters don't
notice.

**One connection per account, stored as an application setting.** There is no
Connections page and no `connections` table. Each connection is one row in the
existing `application_settings` table (see *Data model*), keyed by provider and
account, so signing in to the same Flickr or Google account twice finds the
existing row instead of creating another. Syncs and custom adapters refer to it
by that key.

Why shared rather than one per sync: many OAuth 2 services **rotate refresh
tokens** (each refresh returns a new refresh token and invalidates the old
one), so two copies of the same sign-in would sign each other out. One stored
token per account, refreshed in one place, avoids that, and means one sign-in
covers an import and an export from the same account.

- **Scopes are the union** of what the syncs using it need. A sync that needs
  more than the connection has (an export added to an account first signed in
  for import) asks in its *Account* step ("Also allow Yaffo to add photos to
  jason@gmail.com") and re-runs the sign-in for the union; the row's `scopes`
  is updated in place.
- **Status is shared.** When a call fails with `auth`, the engine marks the
  connection `needs_sign_in`, and every sync using it shows "Sign in again".
  One re-sign-in fixes them all.
- **Token refresh is serialized per connection** without holding a write lock
  across the network call: a short conditional `UPDATE` takes a refresh lease
  (`refreshing_until`); the holder refreshes, writes the keychain, and clears
  the lease; a concurrent run that finds the lease waits briefly and re-reads
  the keychain.
- **Garbage collection.** When a sync is deleted or switched to another
  account, or a custom adapter is deleted, a connection that no sync and no
  adapter refers to is deleted and its token revoked where the provider has a
  revoke endpoint.

Most of a connection comes from its provider (OAuth client id, auth
endpoints, `base_urls`, `followup_hosts`, default scopes). The row stores only
what differs: the provider's stable id for the account, the label shown in the
UI (e.g. the email the provider returns), granted scopes, status, the keychain
`secret_ref`, and instance fields for self-hosted providers (Immich's server
URL).

### Adapter

A Starlark module with a literal `ADAPTER` metadata dict and a fixed set of
**hook functions**. It has a `role` (`source` or `target`), and is either a
**preset** (shipped with Yaffo, code-hash pinned, read-only) or **custom**
(written in the builder, owned by the user).

### Sync automation

An automation whose `sync` column is set. It names an adapter and a
connection, and adds options, a destination, and triggers:

- **Source sync**: adapter + connection + options (e.g. folder) + **import
  root** (a media dir and subfolder the host may write into).
- **Target sync**: adapter + connection + options (e.g. album title) +
  **selection**.

Everything else is ordinary automation machinery: triggers, runs as `Job`s, run
history, enable/disable, Test.

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

Owned by the host and keyed by sync automation. Neither adapters nor selectors write it
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
  `list_remote` (target); `choices` (either).
- Options and hooks must agree: a `remote_folder` or `remote_collection`
  option requires a `choices` hook, and a `choices` hook with no such option
  is an error.
- Capabilities and hooks must agree: `"remove": True` requires a `remove` hook,
  and a `remove` hook without the capability is an error.

The host stores the extracted metadata as JSON (hook values become their
function names), so the UI, the builder prompt, and the consent screen never run
code to learn what an adapter does.

**The selector is a separate script, on purpose.** An adapter is
provider-specific and reusable (presets are shared by every sync that uses them); a
selection is user-specific. Keeping them apart means one Google Photos preset
serves every user's "For Mom" rule, and changing the rule never touches (or
re-consents) the adapter's web grants. The selector lives on the sync's own
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
to the sync's connection), so one adapter works with any connection
of the right preset.

## Host ↔ Starlark contract

### Module metadata

```python
ADAPTER = {
    "contract": 1,                      # host/adapter contract version
    "role": "source",                   # "source" | "target"
    "provider": "onedrive",             # connection preset this adapter needs, or "custom"
    "title": "OneDrive camera roll",
    "options": {                        # the sync's Options form; see Options below
        "folder": {"type": "remote_folder", "label": "OneDrive folder", "required": True},
        "include_videos": {"type": "bool", "label": "Include videos", "default": True},
    },
    "hooks": {"list_page": list_page, "choices": choices},  # name → top-level def
    # target-only: "capabilities", see below
    # custom-only: "connection", see Building a custom adapter
}
```

`ADAPTER` is a literal apart from `hooks` (checked statically, like endpoint
URLs), so the UI, the builder, and the consent screen can read it without
running code.

### Options

Options are **per adapter**: each adapter declares what a sync using it must
be told (which OneDrive folder, which Google album, whether photos are
public), and the host renders that as the sync's *Options* step, validates
the values, and passes them to every hook as `ctx["options"]`. A OneDrive
source and a OneDrive target share a provider but declare their own options.

Each option has a `type`, a `label`, and optionally `help`, `default`, and
`required`:

| type | rendered as | value in `ctx["options"]` |
|---|---|---|
| `string`, `int`, `bool` | the existing fields (`render_configure_form`, as system automations' `config_fields` use) | the value |
| `select` | a dropdown of fixed `choices: [{value, label}]` declared in the option | the chosen `value` |
| `remote_folder` | a **folder browser** on the service: breadcrumb, subfolder list, *Open*, *Choose* | `{"id": "01ABC…", "label": "Pictures › Camera Roll"}` |
| `remote_collection` | a **list of the service's albums, sets, or buckets**, plus *Create new* when the option sets `allow_create: True` | `{"id": …, "label": …}` or `{"create": "For Mom"}` |

**The `choices` hook** feeds both remote types:

```python
def choices(option, parent_id, cursor, ctx):
    """Called by the host when the user opens a picker (parent_id None) or a
    folder inside it. `option` is the option's key."""
    return {
        "items": [{"id": "01ABC…", "label": "Camera Roll",
                   "has_children": True,      # remote_folder: can be opened
                   "suggested": True}],       # preselected when the option has no value yet
        "next_cursor": None,                  # more items; the picker shows "Load more"
    }
```

- It's a **live, read-only** call through the sync's own connection: only
  endpoints granted with `read` risk, the same rule as live reads in Test.
  So the *Options* step stays locked ("Sign in first") until the *Account*
  step is done. Writes made from `choices` are an error.
- A remote value stores the **id and the label**. The id keeps working when
  the folder or album is renamed; the label is what the form and the consent
  sentence show ("Import new photos from *OneDrive › Pictures › Camera
  Roll*"). The label is refreshed when the picker is next opened.
- A remote default can't be a literal (ids differ per account), so adapters
  mark a sensible choice `suggested` instead (OneDrive's *Camera Roll*).
- **At run time** the adapter uses the id. If the service says it no longer
  exists (`not_found` on the first call), the run pauses with
  `option_missing` ("The OneDrive folder *Camera Roll* is gone. Choose
  another."), the same pattern as `selection_missing`. It never falls back to
  a default silently.
- Values are part of the consent hash (see *Consent hash*), so choosing a
  different folder or album re-asks.
- **Labels and help text** of presets go through gettext, keyed by adapter slug
  and option key; custom adapters' labels are whatever the builder wrote.

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
  as `contract_outdated`). Syncs using it pause with `runtime.status =
  "invalid_config"` and run history says why. Nothing runs on an adapter that
  doesn't validate.
- **The fix is regeneration.** The adapter page shows *Regenerate*, which opens
  the builder with the old code, the validation errors, and the current contract
  docs, and asks for a rewrite. The result goes through normal draft → Test →
  publish, including re-authorization if its endpoints change.
- **Presets ship with the app**, so they're always on the current contract.
  Upgrading the app re-seeds them (the `preset_version` rule).

**Stored data is different: it's migrated, not invalidated.** The JSON in
`automations.sync` and `sync_adapters.working`/`published`/`grants` is Yaffo's
own data, not generated code. When its schema
actually changes, the change ships with a data migration (a numbered migration
under `yaffo/scripts/db/migrations/`, per [Project Context](project-context.md))
that rewrites the stored JSON to the new shape. Between schema changes the JSON
is simply validated against the current schema on load. Anything that still
fails (a bug, a hand edit) marks that sync `invalid_config` rather than
being guessed at.

### `ctx` (passed to every hook)

```python
ctx = {
    "options": {...},          # the sync's validated options
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
| `auth` | stop, set the automation to `needs_sign_in`, don't retry |
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
    "accepts": {              # optional; omitted = the original file, whatever it is
        "types": ["image/jpeg", "image/png"],
        "max_bytes": 20000000,
        "max_pixels": 16000000,   # width × height
    },
}
```

**Formats: `accepts` and automatic conversion.** Many targets reject HEIC or
large files (many custom targets, and anything a photo frame reads through the
Folder target).
When a target declares `accepts`, the engine prepares each batch item before
calling `push`:

- an image whose type isn't in `types`, or that's over `max_bytes` /
  `max_pixels`, is converted to a **JPEG copy** (downscaled to fit, quality
  stepped down until it fits `max_bytes`), using the same HEIC support the
  indexer and thumbnailer already use (`yaffo/utils/image.py`);
- the copy keeps the original's capture date, orientation, and location
  metadata, so the target gets the same information the original would have
  carried;
- the batch item describes the copy (`filename: "IMG_1234.jpg"`, its `mime`
  and `size`), and `web_upload_media` sends the copy's bytes. The adapter
  can't tell the difference and never has to convert anything itself;
- **videos aren't transcoded.** A video the target doesn't accept is recorded
  as failed with `rejected` / `unsupported_format` and listed on the run
  ("2 videos weren't sent: this target only accepts images"), rather than spending minutes of CPU
  per clip;
- copies are made lock-free before `push`, kept in a temporary folder for the
  run, and deleted afterwards; the library's original is never modified.
  `sync_items.details` records `sent_as` (type and size) so the Items list can
  show "sent as JPEG".

`accepts` is metadata like the rest of `capabilities`, so the builder declares
it from the service's docs and Test shows the conversions it would do.

Every target is a **collection**: an album, set, or folder whose items persist
and can, in principle, be listed and removed. Send-only destinations (chat
messages, webhooks, email) are out of scope. The engine adapts to the rest of
these capabilities: when a selection change implies removals the target can't
do (`remove: False`), the run reports them ("3 photos left the selection but can't be removed from
this target") instead of silently dropping them.

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

Syncs use the ordinary automation triggers: a schedule (`AutomationTrigger`
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

### Album-selected syncs subscribe automatically

When a target sync's selection is `album`, the sync owns an event trigger
`album_changed` with `config={"album_id": <selected album>}`. It's created with
the sync and updated in the same transaction when the selected album changes
(which also re-asks consent). The user can still add a schedule as a safety net;
the default is both: the event for promptness, a nightly schedule to catch
anything missed (e.g. a run that was paused for sign-in).

Query and script selections can't use `album_changed`. They can subscribe to
the events their rule depends on (`media_indexed`, `media_modified`,
`media_labeled`), or just use a schedule.

### Coalescing bursts

A user curating an album clicks *add* many times in a few minutes. One sync run
per click would mean many small runs racing the per-sync run lock. Instead,
for syncs, an event doesn't start a run directly:

1. Dispatch doesn't invoke the sync. It sets the event trigger's existing
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
exists. Only syncs opt in, via `config.settle_seconds`; other
`album_changed` subscribers are dispatched immediately, like every event today.

The run diffs the whole album, so it doesn't matter how many events were
coalesced or what ids they carried. A deferred run starts with an empty causal
chain (it's started by the tick, not the event). That's safe for targets,
which never write Yaffo albums. If a tick finds the sync's previous run
still holding the run lock, it leaves `next_run_at` set and tries again on the
next tick. `deleted` skips the settle window: the
sync pauses with `selection_missing` right away (see *What we give up by
using JSON*).

### Interaction with the loop guard

Syncs never write Yaffo albums: a target writes only to the remote
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
        skip if sync_items has (sync, source_id) with same updated_at
        skip + link if hash matches an existing media_items.sha1  (cross-library dedupe)
        tombstone → mark the sync_items row deleted_at_source (never delete the local file; see Source deletions)
        resolve_download if needed → download to temp in the import root
        sniff type (images/videos only), enforce size caps, check free space
        atomic rename to Imports/<sync>/<YYYY>/<MM>/<sanitized name>
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
current = sync_items for this sync
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
- A count on the sync's run summary ("12 photos deleted in OneDrive; kept in
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
- `state_patch` size-capped (64 KiB per sync, JSON only).

## Test / preview

| | Source | Target |
|---|---|---|
| Web reads | **live** for the first page (`GET`s and POST-as-read endpoints the adapter has granted, marked `read` risk) | live `list_remote` first page, if granted |
| Web writes | n/a | recorded, not sent (`$ref:N` tokens for returned ids, as today) |
| Shows | the first page as a table of records (filename, taken, size, hash, "already in library?") plus validation errors | the diff ("would add 12 to *For Mom*, remove 3"), then the adapter's recorded web calls per batch |
| Library writes | none, no downloads | none |

**Which account Test uses.** A sync's Test uses that sync's connection. The
builder's Test (`test_adapter`) uses the custom adapter's test connection
(`sync_adapters.test_connection_key`); with none signed in, its reads return
recorded placeholders like its writes.

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
2. **Sync consent**: what the engine will do with it.
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

## UI

A sync is a **type of automation**, next to system automations and custom
(script) automations. It lives in the existing Utilities › Automations list,
uses the existing detail page, triggers pages, and run history, and adds no
new top-level pages. There is no Connections page and no Adapters page.

Mockup: [sync automation pages](assets/automation-sync-adapters/sync-automation-mockup.html)
(a running Google Photos target, a OneDrive import mid-backfill, a Flickr target
mid-setup, a OneDrive target that needs more access on a shared account, and the
*New automation* dialog). It's a rough static HTML sketch, not the final styling.

### Creating one

*New automation* gets a type choice before the name:

| type | what follows |
|---|---|
| **Custom** | today's flow: name, then the builder chat |
| **Import from…** | pick a service: source adapters, presets first, then the user's custom ones |
| **Send to…** | pick a service: target adapters, presets first, then custom ones |
| **Add a new service…** | builder chat for a custom adapter (see *Custom adapters*) |

After the service, setup is one page, filled top to bottom. Every step is a
section of the detail page, so an unfinished setup is just an automation whose
status says what's missing (`needs_sign_in`, `needs_consent`):

1. **Account.** If a connection for this provider exists, it's preselected
   ("OneDrive · jason@outlook.com ✓ · Use a different account"), and if this
   sync needs scopes it lacks, the step asks to extend it. Otherwise *Sign in
   to OneDrive* runs the OAuth flow in the browser (API-key providers show a
   key field, Immich also asks for its server URL). Afterwards the section
   shows the account label, "also used by 2 other syncs" when shared, and a
   *Sign in again* link.
2. **Options.** A form generated from `ADAPTER["options"]` (see *Options*):
   plain fields use the macro system automations use for `config_fields` today
   (`render_configure_form`); `remote_folder` / `remote_collection` options
   open a folder browser or an album list on the signed-in account. Locked
   until the *Account* step is done.
3. **Where.**
   - Source: the import root, using the existing media-dir picker plus a
     subfolder (default `Imports/<provider>`).
   - Target: the selection: *Album* (album picker), *Photos matching a filter*
     (the existing filter UI, saved as the `data_query`), or *Custom rule*
     (opens the builder chat on this automation for a selector script).
4. **When.** Defaults filled in, editable on the existing triggers page:
   nightly for sources; for album targets, *when the album changes* plus
   nightly.
5. **Turn on.** Shows the consent sentence ("Send the photos in *For Mom* to
   Google Photos › *For Mom* as jason@gmail.com") and the endpoint list for
   custom adapters. Confirming stores the consent hash and enables the
   automation. *Test* sits next to it and shows the preview described in
   *Test / preview*.

### The detail page of a sync

Same layout as other automations, with the code section replaced by:

- **Header:** "OneDrive › Camera Roll → Pictures/Imports/OneDrive", status chip,
  on/off toggle.
- **Status line** from `runtime`: "Importing your existing photos: 8,200 so
  far" during a backfill, "Up to date · checked 06:00" afterwards, or the
  action needed ("Sign in again", "Review changes to turn back on", "The album
  *For Mom* was deleted").
- **Setup sections** (above), collapsed once complete.
- **Items:** the `sync_items` ledger for this automation, filterable by status
  (sent, failed, can't remove, deleted at source), with *Retry failed*.
- **Run history:** the existing component; each run's summary carries the
  engine's counts ("+34 imported, 2 skipped as duplicates, 12 deleted in
  OneDrive and kept").

### Custom adapters

A custom adapter is created from *Add a new service…* and is reached
afterwards from any sync that uses it (*Edit service* on the Options
section). It opens the builder chat with the adapter's code, the Web access
panel, Test, and Publish, as in the web-calls doc (see *Building a custom
adapter* for the builder's tools and permissions). When an adapter fails
validation after an upgrade, the syncs using it show "This service needs
updating" with *Regenerate*. Presets never show code.

### In the library

- **Album page:** a chip when a sync sends this album ("Sent to Google Photos ·
  3 failed"), linking to the automation. *Send this album to…* in the album
  menu is a shortcut into *New automation › Send to…* with the selection
  pre-filled.
- **Photo details:** "Imported from OneDrive" / "Sent to *For Mom*", from
  `sync_items`.
- **Filters:** *Imported by* (a sync automation), *Deleted at source*, *Sync
  failed*.

## Building a custom adapter

*Add a new service…* opens the builder chat on a new, empty custom adapter.
The model writes **only the adapter**: the provider-specific hooks (roughly
50–150 lines) plus, because there's no preset to supply one, the provider's
**connection definition**. The sync engine, the item ledger, retries, and file
writes stay in the host, exactly as for presets. Once published, the adapter
appears in *Import from…* / *Send to…* tagged "custom", and any number of syncs
can use it.

Mockup: [Add a new service](assets/automation-sync-adapters/add-service-mockup.html)
(the builder chat with Overview, Test account, Web access, Code, and Test tabs,
stepping through describe → reading docs → test account → live test →
publish).

### The `connection` block

Presets get their connection definition from host code. A custom adapter
declares it in `ADAPTER`, as a literal like the rest of the metadata, so it's
validated statically and shown at *Turn on* without running code:

```python
ADAPTER = {
    "contract": 1,
    "role": "target",
    "provider": "custom",                 # no preset
    "title": "Flickr sets",
    "connection": {
        "auth_kind": "oauth1a",           # oauth2 | oauth1a | api_key | sigv4 | basic | session
        "auth": {
            "request_token_url": "https://www.flickr.com/services/oauth/request_token",
            "authorize_url": "https://www.flickr.com/services/oauth/authorize",
            "access_token_url": "https://www.flickr.com/services/oauth/access_token",
            "scopes": ["write"],
        },
        "base_urls": ["https://api.flickr.com", "https://up.flickr.com"],
        "followup_hosts": [],
        "app_credentials": ["client_id", "client_secret"],  # entered once while building
        "redirect": "registered",         # OAuth only; see "The return address" below
        "app_form_labels": {              # the service's own words, for the instructions
            "redirect": "Callback URL", "client_id": "Key", "client_secret": "Secret",
        },
    },
    "options": {...},
    "capabilities": {...},
    "hooks": {...},
}
```

Rules, checked on write and at publish:

- `auth_kind` must be one of the six in *Sign-in methods* below, with the
  fields that kind requires. A service that needs anything else can't be built
  as a custom adapter; the builder says so instead of trying.
- Every URL is `https`. `base_urls` and `followup_hosts` are plain origins (no
  wildcards beyond a leading `*.` on a registrable domain), and every endpoint
  the static detector finds must fall under them.
- OAuth blocks declare `redirect` (below); `app_form_labels` is optional and
  only changes wording.
- The connection block is part of the adapter's code hash, so changing a host,
  token URL, or scope is a code change: it goes through draft → Test → publish
  and re-asks consent on every sync using it.

### Sign-in methods

**Scripts never see secrets, and there is no script-defined sign-in.** A token,
key, or password enters a request only where the connection block declares
it, filled in by Yaffo (a header, a path or query placeholder, a cookie) or
used by Yaffo to sign the request. A script that did its own sign-in would have
to hold the secret, and a buggy or prompt-injected adapter could then send it
to any host it's allowed to call (in a caption, a title, a request body); the
domain allow-list can't catch that, because the host is legitimate. Keeping
auth declarative keeps that property checkable. Services that need something
outside these six are unsupported until three real services need the same
new kind (the web-calls doc's rule of three).

| `auth_kind` | for | block declares | the user does | secret kept in the keychain |
|---|---|---|---|---|
| `oauth2` | Google, Microsoft, Dropbox, pCloud | authorize + token URLs, scopes, `redirect`, optional `device_code` URL, token-request variations (client secret in body or header, PKCE only, extra parameters) | signs in in the browser, or enters a device code | refresh + access token |
| `oauth1a` | Flickr, SmugMug | request-token, authorize, access-token URLs, `redirect` | signs in in the browser | access token + token secret |
| `api_key` | Immich, Pixelfed personal tokens, Nextcloud file-drop links | where the key goes: `header` (name, optional prefix), `query` (parameter), or `path` (placeholder) | pastes a key, token, or share link | the key |
| `sigv4` | S3, R2, B2 | service, how the endpoint is built (from region or account id) | pastes an access key pair | key id + secret |
| `basic` | Nextcloud and other WebDAV, some NAS | `scheme`: `basic` or `digest` | enters a username and an app password | username + password |
| `session` | Synology Photos, PhotoPrism, Piwigo, Lychee, Backblaze native B2 | a login request, where the token comes from, how it's attached, what "expired" looks like, optional 2FA | enters a username, password, and a 2FA code if asked | username + password (to log in again), plus the current session token |

**`session` in detail.** Many self-hosted servers trade a username and password
for a session token. Yaffo does the exchange; the adapter only ever sees the
token's *effect* (authenticated responses):

```python
"connection": {
    "auth_kind": "session",
    "login": {
        "method": "GET",
        "url": "{base_url}/webapi/entry.cgi",
        "query": {"api": "SYNO.API.Auth", "version": "7", "method": "login",
                  "account": "{username}", "passwd": "{password}",
                  "otp_code": "{otp}", "format": "sid"},
    },
    "token_from": {"json": "/data/sid"},          # or {"cookie": "PHPSESSID"}, {"header": "X-Session-Id"}
    "attach": {"query": "_sid"},                   # or {"header": "Authorization", "prefix": "Bearer "}, {"cookie": "…"}
    "expired_when": {"json": "/error/code", "in": [106, 107, 119]},  # or {"status": [401]}
    "otp": "optional",                             # "none" | "optional" | "required"
    "check": {"method": "GET", "url": "{base_url}/webapi/entry.cgi?api=SYNO.Core.NormalUser&method=get&version=1"},
}
```

- `{username}`, `{password}`, `{otp}` exist only in the `login` request. The
  static checker rejects them anywhere else, including in scripts.
- On an `expired_when` match Yaffo logs in again once and retries the call; a
  failed login sets `needs_sign_in`. Re-login uses the same refresh lease as
  OAuth token refresh, so concurrent runs don't log in twice.
- **2FA:** a one-time code can only be typed by a person, so re-login can't use
  one. When a login needs a code, the sign-in screen asks for it and offers the
  service's "remember this device" option where the login request supports it
  (Synology's `enable_device_token`, whose device token is then stored with the
  password and sent on re-login). If re-login still demands a code, the
  connection goes to `needs_sign_in` and every sync using it says so.
- Because Yaffo must store the password to log in again, the screen recommends
  a dedicated user or an app-specific password where the service has one.

**Every kind declares a `check` call** (`whoami`): a read-only request Yaffo
makes right after sign-in (Pixelfed `verify_credentials`, Immich `/api/users/me`, a `HEAD`
on the chosen bucket for S3). It proves the credential works before any sync
relies on it and supplies the account id and label (see *Connections*). OAuth
kinds may use the ID token or token response instead. Without a usable
`check`, the account id falls back to a fingerprint of the key and instance.

**Secret URLs** (a Nextcloud *file drop* share link, an Immich shared-album
upload link) are `api_key` with `path` placement: the UI asks for "the share
link", and Yaffo splits it into the host (checked against `base_urls`) and the
secret path parts. In the adapter's code the secret parts are **connection
placeholders** (e.g. `https://cloud.example.org/public.php/dav/files/{token}/`),
filled in by the host. The
script never sees their values, and the endpoint detector treats them as part
of the connection, not as script values.

**Self-hosted servers** (Immich, Nextcloud, Synology, PhotoPrism) declare
`instance: ["base_url"]`. The address the user enters is added to the
connection's allowed hosts at run time, shown in consent ("Yaffo will only
talk to immich.home.lan"), and part of the account id, so two servers are two
connections. *Open question:* plain `http` on a home network. https only is
safest, but many home servers are http-only on the LAN. Proposed: allow
`http` only for a private-network address (RFC 1918, `.local`, `.lan`) after an
explicit warning, never for a public address.

**Device code as a fallback.** A preset whose provider supports both (OneDrive,
some Google scopes) shows a "Signing in on another device?" link under the
normal button, which switches to the device-code screen. That covers Yaffo
running on a home server opened from a laptop, where the browser can't reach
Yaffo's local return address.

**Not supported:** client certificates (mTLS), signed-JWT service accounts,
OAuth 2 client credentials (server-to-server setups, not personal photo
services), and cookie sessions scraped from a website's login (iCloud, Google
Photos without the API).

**App credentials and the test account.** Yaffo has no registered app with an
unknown service, so an OAuth custom adapter needs the user to register their
own developer app once. The builder's **Test account** tab has two parts:

1. **App credentials** (only when the connection block lists
   `app_credentials`): fields for the client id and secret, labelled with the
   service's own words from `app_form_labels` ("Key", "Secret" on Flickr), and,
   only when `redirect` is `registered`, Yaffo's return address as a
   copy-paste step in the same words ("On Flickr's *Create an app* form, paste
   this into **Callback URL**"). The builder explains the service's
   registration steps from its docs. The credentials are stored in the
   keychain under the adapter (`app_secret_ref`), never in the database, never
   sent to the LLM provider, and reused by every sync that uses the adapter.
2. **Test account**: *Sign in to Flickr* creates (or picks) an ordinary
   connection and records it as the adapter's `test_connection_key`, which
   `test_adapter` uses. It stays after publishing, so later edits and
   *Regenerate* can be tested live.

**The return address.** In an OAuth sign-in the user approves access on the
service's site, which then sends the browser back to Yaffo with a one-time
code. Services differ in whether that return address has to be registered with
the app in advance, so the connection block declares which case applies and
Yaffo only shows the address when the user has to register it:

| `redirect` | the service… | the user sees |
|---|---|---|
| `registered` | only returns to addresses registered with the app | the copy-paste step above |
| `any_loopback` | accepts any `127.0.0.1` / `localhost` address (the RFC 8252 rule for desktop apps; e.g. Google's *desktop app* client type) | nothing |
| `per_request` | takes the address in each sign-in request (common with OAuth 1.0a) | nothing |
| `device_code` | supports device sign-in (RFC 8628): the user types a short code on the service's site; no return address at all | the code and a link, during sign-in |

The builder decides from the docs, like `auth_kind`. A wrong declaration shows
up the same way: signing in the test account fails (the service rejects the
return address), and the error goes back to the builder to fix.

The return address must stay **the same across restarts**, since a registered
address has to match exactly: it's built from the configured web port
(`web.port`, default 5001), never a random one. If the port changes, every
custom adapter with `redirect: registered` shows "Update the return address on
Flickr" with the new address, and its connections go to `needs_sign_in` until
the user does.

Mockup: [account types](assets/automation-sync-adapters/account-types-mockup.html)
(the *Account* step for each sign-in method, return-address case, and the
`session` login with 2FA, with what each stores).

Because the connection is shared, the first sync set up with this adapter
finds the account already signed in and just confirms it. API-key services
(Immich, Pixelfed, most self-hosted tools) have no app registration: signing
in just asks for a key.

A custom adapter's connections are stored with `provider: "custom:<adapter
slug>"`; the auth endpoints, hosts, and scopes are read from the adapter's
`published.metadata.connection` (the working version while building), not
copied into the connection row.

### Where the model's API knowledge comes from

Three sources, from least to most reliable:

1. **What the model already knows.** Good enough for a first draft against big,
   stable APIs; unreliable on field names, versions, and deprecations. Never
   trusted on its own.
2. **Documentation**, through the builder tools below. An OpenAPI spec is the
   best input: the host condenses it to a compact endpoint list (method, path,
   parameters, response fields) rather than pasting raw HTML into the prompt.
3. **Live responses in Test.** The decisive one. After the user signs in, Test
   runs the adapter's granted read calls for real and the model sees the actual
   JSON: write → test → read the real response → fix the mapping. Docs get the
   model close; real responses get it right, and catch docs that are wrong or
   out of date.

### Builder tools

The custom-adapter builder gets its own tool set, alongside today's
`write_automation_code` / `test_automation_code`:

| tool | does | available |
|---|---|---|
| `write_adapter_code` | saves the draft; returns static-check errors (metadata, hooks, connection block, endpoint manifest) | always |
| `test_adapter` | runs Test (see *Test / preview*); returns records or the diff plus recorded calls, trimmed for the model | always; live reads need permission 3 below |
| `fetch_docs(urls)` | fetches a **batch** of pages or OpenAPI specs **from Yaffo** (not the LLM provider) and returns readable, size-capped text per URL | URLs on authorized domains run at once; the rest wait for one batch prompt (permission 1) |
| `web_search(query)` | runs a search **from Yaffo** through the configured search backend and returns titles, URLs, and snippets (no page bodies; the model reads results with `fetch_docs`) | once the backend's domain is authorized (permission 2) |

Both web tools are ordinary builder tools that **Yaffo** executes, so they work
the same with every LLM provider and every request goes through Yaffo's own
authorization. The LLM providers' built-in search tools (Anthropic's
`web_search` server tool and the like) are deliberately **not** used: they run
inside the model call, so Yaffo can't show the query or ask before it runs, and
they exist for only some LLM providers.

**Search backend.** `web_search` needs a search API behind it. Proposed: a
pluggable backend, configured in Settings next to the LLM settings, with its
API key in the keychain like the LLM keys. Candidates are a hosted search API
with a free tier (e.g. Brave Search API) or a self-hosted SearXNG URL. With no
backend configured, the tool isn't offered and the model works from the docs
URLs it already knows (most services' API docs live at well-known places, such
as `flickr.com/services/api`), fetched with `fetch_docs` as usual.

None of these tools exist in the `adapter` host profile: an adapter at run time
can make only its granted calls.

### Permissions

Each thing the builder does on the user's behalf is a separate permission,
because each carries a different risk. Prompts use the global confirm dialog or
an inline chat card, never `confirm()`.

**Web access is authorized by domain, deterministically.** Neither Yaffo nor
the user judges the content of a request (a URL's path, a search query) to
decide whether it may run. A request runs if and only if its domain is in the
adapter's authorized set; otherwise it waits for the user to authorize that
domain. The same inputs always give the same decision, so it's testable and
can't be argued with by a model.

- **Domain** means the registrable domain (eTLD+1, from a bundled snapshot of
  the Public Suffix List, never fetched at run time): authorizing `flickr.com`
  covers `www.flickr.com` and `api.flickr.com`; `alice.github.io` and
  `bob.github.io` are different domains because `github.io` is a public suffix.
- **The authorized set** is stored on the adapter
  (`sync_adapters.doc_domains`) and lasts for its conversation, including
  later edits and *Regenerate*. It starts **empty**: a domain is added only
  when the user approves a request the model actually makes. A URL appearing
  in the chat (typed by the user or written by the model) authorizes nothing;
  if the model never fetches it, nobody is asked about it.
- **Redirects** are followed only within authorized domains. A redirect to a
  new domain stops there and comes back to the model as "needs
  authorization", so it lands in the next batch prompt.

| # | action | risk | how it's approved |
|---|---|---|---|
| 1 | Read documentation (`fetch_docs`) | an outbound request from a local-first app; the content is untrusted | **one prompt per batch**, listing only the domains in the batch that aren't authorized yet, each with its URLs: "The builder wants to read 4 pages. Already allowed: flickr.com (2). New: githubusercontent.com (1), example.org (1). Allow all / choose / Deny". URLs on already-authorized domains are fetched without asking; a batch with no new domains shows no prompt. Denied URLs come back to the model as denied |
| 2 | Web search (`web_search`) | queries go to the search backend; a hosted backend may bill per query | the search backend is treated as a domain like any other (`brave.com` for Brave Search): the first search asks "Allow the builder to search the web through Brave Search?", and later searches run without asking. Results are opened with `fetch_docs`, under row 1. A per-conversation cap on searches (e.g. 10) stops a looping model and bounds cost; it's a count, not a judgment |
| 3 | Live read calls in Test, using the adapter's test connection | real account data goes into the model's context, and so to the LLM provider | the user clicking *Test* is its own approval. For the builder to run `test_adapter` live on its own, the user approves once per adapter: "Let the builder make read-only calls to your Flickr account (jturan) while building. Responses are sent to the AI model." Limited to granted `read`-risk endpoints; without it, model-initiated tests use recorded placeholders |

Writes are never sent from Test under any permission; they're recorded, as for
every Test. The host caps what goes back to the model from live calls (first
few records, long fields trimmed, response headers dropped except paging and
rate-limit ones).

### Fetched docs are untrusted

A docs page or search result is text from the internet in the model's context,
and could tell the model to add a call to some other host or to send data
somewhere. Nothing the model writes takes effect without passing through the
checks that already exist:

- endpoints are detected statically and must be under the connection block's
  hosts;
- the hosts are listed in plain words at *Turn on* ("This service talks to
  api.flickr.com and up.flickr.com") and changing them re-asks consent;
- endpoints marked `shares_externally` keep their separate confirmation;
- the adapter can't read files or Yaffo data except what the engine hands each
  hook.

So a poisoned docs page can make the model write a bad adapter, but the user
sees any unexpected host or endpoint before it runs.

### What can't be built

- Services with no public API, or whose API is closed to new apps (iCloud
  Photos, Instagram's personal-account API).
- Sign-in methods outside the six in *Sign-in methods* (custom signing
  schemes, client certificates, scraped web logins).
- Sources that need a person in the loop or local OS access (see *Sources that
  aren't adapters*).

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
        url = url_template(GRAPH + "/me/drive/items/{id}/delta",
                           id=ctx["options"]["folder"]["id"])
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

def choices(option, parent_id, cursor, ctx):
    items = []
    if cursor:
        url = cursor
    elif parent_id:
        url = url_template(GRAPH + "/me/drive/items/{id}/children", id=parent_id)
    else:
        url = GRAPH + "/me/drive/root/children"
        roll = web_request(CONNECTION, "GET", GRAPH + "/me/drive/special/cameraroll")
        if roll["status"] == 200:
            items.append({"id": roll["json"]["id"], "label": "Camera Roll",
                          "has_children": True, "suggested": True})
    r = web_request(CONNECTION, "GET", url)
    if r["status"] != 200:
        return {"error": {"code": "retry_later", "message": "graph %d" % r["status"]}}
    items += [{"id": i["id"], "label": i["name"], "has_children": i["folder"]["childCount"] > 0}
              for i in r["json"]["value"] if "folder" in i]
    return {"items": items, "next_cursor": r["json"].get("@odata.nextLink")}

ADAPTER = {"contract": 1, "role": "source", "provider": "onedrive",
           "title": "OneDrive folder",
           "options": {"folder": {"type": "remote_folder", "label": "OneDrive folder",
                                  "required": True}},
           "hooks": {"list_page": list_page, "choices": choices}}
```

`ADAPTER` comes last because Starlark evaluates the module top to bottom: the
`hooks` names must already be defined. `to_record` is a private helper. The
folder is stored by id, so renaming it in OneDrive doesn't break the sync.

Note `nextLink`/`deltaLink` are full URLs returned by a granted call. They pass
the grant check through the follow-up-URL rule (the host recognizes URLs it
returned), so the script doesn't need a literal for them.

### Google Photos target

```python
API = "https://photoslibrary.googleapis.com/v1"

def push(batch, ctx):
    albums = dict(ctx["state"].get("albums", {}))
    pinned = ctx["options"].get("album")       # set only if the user picked an existing album
    results, by_target = [], {}
    for item in batch:
        by_target.setdefault(item["target"], []).append(item)
    for target, items in by_target.items():
        if pinned and "id" in pinned:
            albums[target] = pinned["id"]
        elif target not in albums:
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

def choices(option, parent_id, cursor, ctx):
    ...  # GET albums?pageToken=… ; Google returns only albums this app created

ADAPTER = {"contract": 1, "role": "target", "provider": "google_photos",
           "title": "Google Photos album",
           "options": {"album": {"type": "remote_collection", "label": "Google Photos album",
                                 "help": "Leave empty to use the Yaffo album's name.",
                                 "allow_create": True}},
           "capabilities": {"max_batch": 50, "remove": True, "update": False,
                            "reconcile": True, "targets": "many"},
           "hooks": {"push": push, "remove": remove, "list_remote": list_remote,
                     "choices": choices}}
```

The remote album normally comes from the **selection**: each routing target
(by default the Yaffo album's name) gets a Google album of that name, created
on the first run and remembered by id in `state.albums`. The optional
`album` option pins one existing album instead. Its picker can only list
albums **Yaffo created**: since Google's 2025 Library API changes, an app can
list and add to only its own albums, and the picker says so ("Only albums
Yaffo created can be used").

## Sources that aren't adapters

Some imports have no listing API but can still feed the same engine as
**host-native record producers** (Python, reviewed, one per kind). They emit the
same record schema, so dedupe, file writes, metadata merge, and indexing are
shared. The ones a user sets up as a sync (Folder) are `sync_adapters` rows with
a `handler` instead of code, like built-in targets; the Picker and Takeout are
one-off utility actions:

| producer | why it isn't an adapter |
|---|---|
| Google Photos Picker | needs a person in the loop (a picker session in their browser); a utility button, not a schedule |
| Google Takeout ingester | a local ZIP with JSON sidecars; no network |
| macOS Photos (osxphotos / PhotoKit) | local bridge with macOS permissions, not web calls |
| **Folder** | local files, no web calls (below) |

### Folder (source)

*Import from… › Folder* **copies** new photos from a folder into the import
root. It's the mirror of the Folder target, for folders that aren't a
permanent home: an SD card or camera, a desktop sync app's camera-upload
folder the user prunes (Dropbox, OneDrive, Google Drive, which gives a
Dropbox import with no API), Downloads, or a family member's shared folder.

It differs from adding the folder as a **media folder** (Settings), which
indexes files in place:

| | media folder (exists today) | Folder source |
|---|---|---|
| files | stay put; the folder becomes part of the library | copied into `Imports/<name>/YYYY/MM` |
| folder cleaned out (card reformatted, uploads emptied) | the photos leave the library | the library's copies stay |
| files already in the library | indexed again as duplicates | skipped (content hash match) |

The setup says so: "Want to keep these photos where they are? Add the folder
as a media folder instead."

- **Options:** the folder, include subfolders (on by default), photos /
  videos / both.
- **Records:** one per file. `source_id` is the path relative to the folder;
  size + modification time act as `updated_at`; the content sha1 is the
  `hash`, so files already in the library are linked, not copied. No date or
  location hints: the file's own metadata is used.
- **Originals are never touched,** and there's no "delete after import". The
  user can clear the card themselves once they've checked the import.
- **No tombstones.** Files disappearing from the folder is normal (a
  reformatted card, an emptied upload folder), so the producer never emits
  `deleted: True` and nothing is marked `deleted_at_source`.
- **Missing folder:** if the card or drive isn't connected, the run ends with
  "waiting for the folder" as an ordinary outcome, not an error or a pause.
- **Guards:** the folder can't be inside a library media folder or any sync's
  import root (Yaffo would import its own files), checked on save and at run
  start.
- **Triggers:** a schedule or *Run now*. A "when this drive is connected"
  event (a new `volume_mounted` domain event, filtered by volume) is the
  natural trigger for cards and is left for later.
- **No account, no connection.** Consent: "Copy new photos from
  */Volumes/EOS_DIGITAL/DCIM* into *Pictures › Imports › Camera*."

## Targets that aren't adapters

Some targets don't make web calls, so they can't be Starlark adapters. They're
**built-in targets**: reviewed Python that plugs into the same engine in place
of `push` / `remove` / `list_remote`. Everything else is shared with adapter
targets: selection (album, filter, rule), the `album_changed` trigger and
settle window, the item ledger, `accepts` conversion, Test, consent, and the
*Send to…* setup. Each is a `sync_adapters` row with `is_preset` set and a
`handler` naming the Python implementation instead of code.

### Folder

*Send to… › Folder* keeps a copy of the selected photos in a folder outside the
library. Whatever watches that folder takes it from there:

| what watches the folder | result |
|---|---|
| the Amazon Photos desktop app's folder backup | photos reach Amazon Photos (and an Echo Show showing them) |
| Dropbox, Google Drive, OneDrive, or Synology Drive desktop apps | photos sync to that service with no API, app registration, or sign-in |
| a NAS share or mounted network drive | a plain copy elsewhere |
| a USB stick or SD card | load it into a photo frame that reads cards (most do, Frameo included) |

**Options** (rendered like any adapter's `options`):

| option | values | default |
|---|---|---|
| Folder | a folder picker; must exist and be writable | none |
| Layout | flat · one subfolder per routing target (album) · by year/month of capture | one subfolder per target |
| Convert to JPEG | off · on (sets `accepts` to JPEG/PNG) · on, sized for a photo frame (also caps pixels and bytes) | off |
| When a photo leaves the selection | delete Yaffo's copy · keep it | delete Yaffo's copy |

**Capabilities:** `kind: collection`, `targets: many`, `remove` and `reconcile`
supported, `update: false` (captions aren't written; the copy keeps the
original's embedded metadata), `accepts` from the *Convert* option.

**Writing.** Each item is copied to a temporary name in the destination, then
renamed into place, so a watcher never sees a half-written file. The file's
modification time is set to the capture date (sync clients and frames sort by
it). Name collisions get a suffix (`IMG_1234 (2).jpg`). The copy's path,
relative to the folder, is the item's `remote_id`; its size and modification
time go in `sync_items.details`. Copies are made lock-free and the ledger is
committed per batch, as for web targets.

**Deleting is limited to Yaffo's own unchanged copies.** On removal the engine
deletes a file only if the ledger says Yaffo wrote it *and* its size and
modification time still match; anything else (a file the user edited, or one
Yaffo never wrote) is left alone and the item is marked `cant_remove` with a
note. Deleted files go to the OS trash, like library deletes. Yaffo never
lists, renames, or deletes anything else in the folder.

**Reconcile and crash recovery** scan only the paths in the ledger: a `pending`
row whose file exists with the expected size is marked sent; a sent copy the
user deleted is marked `removed_remotely` and not re-copied unless they ask,
the same rule as for web targets.

**Guards.**

- The folder can't be inside a library media folder or Yaffo's thumbnail
  folder (Yaffo would index its own exports as new photos), checked on save
  and at run start.
- If the folder is missing (an unplugged drive or unmounted share), the sync
  pauses with `destination_missing` rather than failing each item, and resumes
  when it's back. A run checks free space before copying a batch.
- The folder has to be reachable from the computer Yaffo runs on. A desktop
  sync app on a laptop can't see a folder on a headless home server, and the
  setup says so when the path looks remote.

**What the user is told.** Yaffo knows only that a copy was **written**, not
whether a desktop app uploaded it, so the run summary and Items list say
"written to folder", never "sent". The consent sentence names the folder and
the removal behavior: "Copy the photos in *For Mom* to *~/Amazon Photos
Backup* as JPEG. Photos removed from the album are deleted from that folder."
The *Turn on* step adds what that means downstream when the user picks a known
sync app's folder: Amazon's folder backup only uploads, so deleting a copy
doesn't remove the photo from Amazon Photos, while a Dropbox folder would.

## Targets considered and not planned

| target | why not | what covers it instead |
|---|---|---|
| **Email** (send photos to an address, e.g. a photo frame's) | email isn't an HTTP API, so it can't be an adapter: it would need a built-in target (see above) (Gmail `gmail.send` / Graph `Mail.Send` over OAuth, or SMTP with an app password) plus an `smtp` connection kind for that target only. Sent mail can't be removed (send-only destinations are out of scope, see *Target capabilities*), and frames and personal mail accounts have per-email and daily limits. The audience is small: owners of email-enabled frames (Skylight, Pix-Star, Nixplay) | **deferred.** Revisit if users ask; `accepts` (above) already covers the format half |
| **Amazon Photos** | no public upload API: Amazon closed its Cloud Drive API to new developers years ago and shut Amazon Drive down at the end of 2023 (as of this writing). The only ways in are Amazon's own apps | the **Folder** target, pointed at a folder the Amazon Photos desktop app backs up |
| **Frameo frames** | no API and no email; photos arrive only from the Frameo phone app, peer to peer | the **Folder** target writing to a USB stick or SD card the frame reads |
| **Photo frames generally** | most consumer frames have no public API | frames and smart displays that show a **cloud album** (Google Nest Hub, Nixplay and some Aura models with Google Photos) are covered by the Google Photos target: send an album there and point the frame at it. Frames that read a USB stick or SD card are covered by the **Folder** target |

## Data model

**Rule: configuration and per-sync state are JSON; the per-item ledger is a
table.** Sync configs, grants, adapter state, and cursors are always read and written
whole, one sync at a time, by the engine. Nothing queries *across* them
except a few small admin lookups. They are validated in code (named
dataclasses plus `jsonschema`, which Yaffo already uses for `data_query`) at
every write and at load. Item rows are different: there can be tens of
thousands per sync, the engine looks them up by key and status on every
page, they join to `media_items`, and they must follow media deletes. That
needs a table.

So this design adds **two tables, three columns**, plus connection rows in the
existing `application_settings` table:

```
 application_settings ◄── automations.sync.connection_key       (JSON reference)
   ("sync_connection:…" rows)   ◄── sync_adapters.test_connection_key
 sync_adapters ◄───────── automations.sync.adapter_id           (JSON reference)
   (code, metadata, manifest, grants: JSON)
 automations
   .sync  (JSON: config, consent, state, cursors)
   .working_code / .published_code  (selector script, existing columns)
     └──< sync_items ──► media_items
 conversations.sync_adapter_id ──► sync_adapters   (builder chat for a custom adapter)
 media_items.sha1                                  (dedupe)
```

There is no `connections` table: connections are rows in
`application_settings` (below). The secret itself is in the OS keychain, named
by `secret_ref`; token refreshes write only the keychain (and the lease), never
the token into the database. All changes follow the migration rules in
[Project Context](project-context.md).

### Connections (`application_settings` rows)

`ApplicationSettings` is the existing key/value table (`name` unique, `type`,
`value` as a string). Each connection is one row:

| column | value |
|---|---|
| `name` | `sync_connection:<provider>:<account_id>`, e.g. `sync_connection:onedrive:4f1a9c…`. The unique `name` is what makes "one connection per account" hold |
| `type` | `json` (a new type value; existing rows use `string`) |
| `value` | a `SyncConnection` serialized as JSON (see *JSON shapes*) |

`account_id` is the provider's stable id for the signed-in user (Google's
`sub`, the Microsoft account id, Flickr's `nsid`, Immich's user id), from the
sign-in method's `check` call or token response. For `basic` and `session` it's
the username plus the server. For a service that can't report one, it's a
fingerprint of the key (and instance URL), so the same key is recognized and a
different key is a different connection.

Access goes through a small repository (`yaffo/sync/connections.py`:
`get`, `find_for_provider`, `upsert_after_sign_in`, `mark_needs_sign_in`,
`take_refresh_lease`, `release_if_unreferenced`), not ad-hoc queries, so the
JSON is validated on every read and write like the rest of the sync JSON.
Nothing else in `application_settings` starts with `sync_connection:`.

### `sync_adapters` (table, mostly JSON)

A table because adapters are shared: one preset serves many syncs, and a
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
    handler = db.Column(db.String, nullable=True)                  # built-ins only ("folder_source",
                                                                   # "folder_target"): Python implementation;
                                                                   # code columns are NULL

    working_code = db.Column(db.Text, nullable=True)
    published_code = db.Column(db.Text, nullable=True)
    working = db.Column(db.JSON, nullable=True)                    # AdapterVersion for working_code
    published = db.Column(db.JSON, nullable=True)                  # AdapterVersion for published_code
    grants = db.Column(db.JSON, nullable=False, default=list)      # [WebGrant]

    doc_domains = db.Column(db.JSON, nullable=False, default=list)  # custom only: registrable domains the builder
                                                                   # may fetch docs from / search through
    app_secret_ref = db.Column(db.String, nullable=True)           # custom OAuth only: keychain entry with the
                                                                   # user's developer-app client id + secret
    test_connection_key = db.Column(db.String, nullable=True)      # custom only: application_settings name of the
                                                                   # connection the builder's Test uses

    status = db.Column(db.String, nullable=False)                  # draft | ready | invalid
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
```

### `automations.sync` (new JSON column)

A sync *is* its automation, so its configuration goes on the `automations`
row rather than a 1:1 table. Dispatch routes an automation with a non-NULL
`sync` to the sync engine (a third route in `automation_dispatch.py`, next to
system handlers and custom code). The existing `working_code` /
`published_code` hold the selector script when `selection.kind == "script"`.

The column holds two parts with different writers, which the engine keeps
apart:

- `config`: written only by the UI/builder (and re-consent).
- `runtime`: written only by the engine, in the same transaction as the page or
  batch of `sync_items` it describes.

### JSON shapes (validated in code)

Named frozen dataclasses in `yaffo/sync/schemas.py`, each with `from_dict` that
runs a `jsonschema` validator and `to_dict` for storage. Loading invalid JSON
raises, and the engine pauses the sync (`status: "invalid_config"`) rather
than running on a guess.

```python
@dataclass(frozen=True)
class SyncConnection:                 # application_settings value for "sync_connection:…"
    provider: str                     # preset key: "onedrive", "immich", ... (supplies auth kind,
                                      # endpoints, client id, base_urls, followup_hosts), or
                                      # "custom:<adapter slug>" (read from the adapter's connection block)
    account_id: str                   # provider's stable user id (or key fingerprint); part of the name
    account_label: str | None         # shown in the UI, e.g. the email the provider returns
    scopes: list[str]                 # granted; the union of what the syncs using it need
    status: str                       # ready | needs_sign_in
    secret_ref: str                   # keychain entry (keyring): token, key, or (basic/session)
                                      # username + password and any device/session token
    instance: dict                    # self-hosted fields only, e.g. {"base_url": "https://immich.home"}
    refreshing_until: str | None      # refresh lease (see Connection)

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
class SyncConfig:                     # automations.sync["config"]
    role: str                         # source | target
    adapter_id: int
    connection_key: str | None        # application_settings name of the connection; None only for
                                      # built-ins with no account (Folder source/target)
    scopes_needed: list[str]          # what this sync needs; must be ⊆ the connection's scopes
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

Stored example (a OneDrive source), with its connection row
`sync_connection:onedrive:4f1a9c…` holding `{"provider": "onedrive",
"account_id": "4f1a9c…", "account_label": "jason@outlook.com", "scopes":
["Files.Read", "offline_access"], "status": "ready", "secret_ref":
"yaffo.sync.onedrive.4f1a9c", "instance": {}, "refreshing_until": null}`:

```json
{
  "config": {
    "role": "source", "adapter_id": 3,
    "connection_key": "sync_connection:onedrive:4f1a9c…",
    "scopes_needed": ["Files.Read", "offline_access"],
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
    """One item a sync has seen (source) or sent (target). Unique on
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
    remote_id = db.Column(db.String, nullable=True)                # target: provider id, or the path relative to
                                                                   # the folder (Folder target); sources: = item_key
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
  `pending`?" for one sync, thousands of times per backfill. Keys and indexes
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
| `automations` | add `sync` (JSON, nullable) | sync config and runtime state (above) |
| `application_settings` | no schema change; new `sync_connection:…` rows of a new `json` type | connections (see *Connections*) |
| `media_items` | add `sha1` (String(40), nullable, indexed) | cross-library dedupe on import; match Immich/OneDrive hashes before downloading. Computed at index time; existing rows backfilled by a maintenance task |
| `conversations` | add `sync_adapter_id` (FK → `sync_adapters`, CASCADE, nullable) | the builder chat for a custom adapter, like `automation_id` for automations |

### What we give up by using JSON, and how code covers it

| lost | handled by |
|---|---|
| FK `RESTRICT` on deleting a custom adapter in use | the delete route checks for syncs first. Automations are few, so a scan of `automations.sync` (or SQLite `json_extract`) is fine. The UI lists the syncs that block the delete |
| FK from a sync to its connection row | the engine loads the connection at run start; a missing or unreadable row sets `needs_sign_in` rather than failing obscurely. Garbage collection (see *Connection*) only deletes rows no sync or adapter names |
| FK `SET NULL` on deleting the selected album | the engine checks the album exists at run start; if not, the sync pauses (`paused`, code `selection_missing`) rather than treating "no album" as "remove everything remotely" |
| Column types/constraints | `jsonschema` on every write and load, plus a drift-guard test that each dataclass's fields match its schema |
| Partial updates | `config` and `runtime` are always replaced whole by their single writer; one run per sync at a time (the engine takes a per-automation run lock), so there are no concurrent writers to one blob |
| Schema evolution | JSON shapes carry no version of their own; a shape change ships with a data migration that rewrites `automations.sync`, like any other migration |

### Consent hash

`config.consent_hash` is a hash over: role, adapter id and
`published.code_hash` (or `handler` for a built-in), the connection key
(provider and account) and the sync's `scopes_needed`, the adapter `options`,
`import_root`, and selection (kind plus album id, query, or the selector's
published code hash). The engine recomputes it at run start; a mismatch sets
`runtime.status = "needs_consent"` and the run stops before any web call or
file write. Because the selection's query and the selector's code hash are
part of the hash, every new version of the selection needs re-authorization
by construction. Options are included because several decide where photos go
or who sees them (the Folder target's path and removal setting, a remote
album title, Flickr's "make public"). The UI asks at publish time, so this
check at run start is only a backstop.

### Lifecycle rules

- **Delete a sync automation**: cascades its `sync_items` (the config goes
  with the row). Its connection is deleted, and its token revoked, only if no
  other sync or adapter refers to it. Nothing is removed remotely and no
  imported file is deleted.
- **Sign in again** (expired or revoked token): replaces the connection's
  keychain entry in place and sets it `ready`, for every sync using it. Signing
  in as a *different* account creates or picks a different connection, which
  changes the consent hash, so that sync re-asks.
- **Delete a custom adapter**: refused while syncs reference it. Otherwise
  deletes its app credentials from the keychain and releases its test
  connection (deleted if nothing else refers to it). Presets can't be deleted.
- **Change a custom adapter's app credentials**: tokens are issued to an app,
  so every connection for that adapter's provider goes to `needs_sign_in`,
  and with them every sync using it. The UI says so before saving.
- **Republish a custom adapter**: new `published.code_hash`. Grants whose hash
  no longer matches stop authorizing calls. If the new manifest only narrows,
  grants are re-stamped automatically; otherwise the publish modal asks.
  Syncs using it get `needs_consent` only if the grant set changed.
- **Preset upgrade** (`preset_version` bump on app update): re-seeded code goes
  through the same rule. A widened manifest pauses the affected syncs with
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
