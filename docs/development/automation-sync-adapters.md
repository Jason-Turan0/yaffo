# Sync Adapters — Sources and Targets over a Host Sync Engine (Design Sketch)

**Status: Phases 1 and 2 built; Phase 3 cloud target code, local tests, and a
live Google Photos upload verified. Crash recovery and OAuth verification remain.
Phase 4 (peer sync from paired devices) built and verified between two instances
on one machine; the two-machine run, the user guide, translations, and browser
specs remain. Phases 5–6 proposed. Alternative to
[Automation Web Calls](automation-web-calls.md).**
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

### Selection

For a target, what leaves Yaffo, and where it goes. One of:

| kind | definition | when |
|---|---|---|
| `album` | a Yaffo album id; membership mirrors to the target | simplest; the user curates by hand |
| `query` | the filter panel's selections, stored in the sharing wire format (`media_filter_params.wire_filters`: only the filters that are set, distances in km), re-evaluated each run | "Mom, 2024, favorites" |
| `script` | a selector Starlark script (read-only automation profile) | routing to several albums, computed captions |

All three produce the same thing: the **desired set**, a list of
`{media_item_id, target, caption}`. `target` is a routing key interpreted by the
adapter (e.g. an album title); `caption` is optional text.

Sources have no selection: they import everything they list. The exception is the
peer source's *What to import*, a `query` selection the paired device applies to
its own library (`SyncConfig.source_filters`; see
[Peer sync](#peer-sync-importing-from-paired-devices)).

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
function names), so the UI, the builder prompt, and the future enable review never run
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
- **Web time doesn't count against the 60 s.** `run_starlark`'s deadline
  normally includes host-call time, which a batch of large uploads would blow
  through. In the `adapter` profile the deadline is paused while a web host
  function (`web_request`, `web_upload_media`) runs; each of those has its own
  timeout (connect + idle-read, so a slow but progressing upload isn't
  killed). The 60 s then bounds the script's own work. The host also chunks
  target batches by total bytes (`capabilities.max_batch_bytes`) as well as by
  count, so one hook call can't be handed hours of uploads.
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
URLs), so the UI, the builder, and the future enable review can read it without
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
| `remote_collection` | a **list of the service's albums, sets, or buckets** | `{"id": …, "label": …}` |

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
| `cursor_invalid` | sources: the cursor or checkpoint has expired (e.g. OneDrive's 410 `resyncRequired`). Drop it and restart: from the checkpoint if only the page cursor failed, otherwise a full pass. Dedupe by `source_id` and sha1 makes the re-listing cheap |

Hooks can return an error for the whole call or per item (in `results`).
**A hook-level `error` may come with partial `results`.** The host first
commits the results it got (and the `state_patch`), then applies the error by
code. For target hooks, batch items with no result are put back as **unsent**,
not left `pending`: the adapter is saying it never got to them, so they don't
need crash recovery and are simply pushed again on the next attempt.
`pending` is reserved for items whose outcome is genuinely unknown (the run
died mid-call).

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
never advances the delta point. A provider that expires cursors returns
`cursor_invalid` (see the error table), and the host restarts the pass rather
than failing the sync.

**Record schema** (validated by the host; invalid records fail the page):

```python
{
    "source_id": "A1B2C3",                  # required; stable provider id
    "filename": "IMG_1234.HEIC",            # required; sanitized by the host
    "folder": "2024/July",                  # optional; the file's folder under the chosen source
                                            #   folder, for the keep-the-source's-folders layout
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
            {"key": "812", "remote_id": "AF1Qip…"},
            {"key": "813", "error": {"code": "rejected", "message": "unsupported format"}},
        ],
        "state_patch": {"albums": {"For Mom": "ALB123"}},
    }

def remove(items, ctx):
    """Optional (capability `remove`). items: [{key, media_item_id, remote_id, target}];
    media_item_id is None when the photo was deleted from Yaffo."""
    return {"results": [{"key": "812"}], "state_patch": {...}}

def update(items, ctx):
    """Optional (capability `update`). Caption or routing-target changes for items
    already pushed. items: [{key, media_item_id, remote_id, target, caption,
    previous: {target, caption}}]. A routing change is a move: e.g. remove from
    the old album and add to the new one, keeping remote_id where the provider can."""
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
    "key": "812",                 # the item's ledger row id (sync_items.id); every result names it
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

**Results are matched on `key`.** Every item the host hands to `push`,
`remove`, or `update` carries `key`, the id of the ledger row it came from
(`sync_items.id`, as a string), and each result must copy it, so the host
knows which row a result is about. The adapter treats it as an opaque label,
like a request id. Results can come back in any order (adapters often group
items by album). `media_item_id` can't serve, because it's `None` for a photo
deleted from Yaffo (see *Photos deleted from Yaffo*); `remote_id` can't
either, because one remote item can be in several targets; and the row id,
unlike `item_key`, never changes, even when a photo moves between targets.

**Target capabilities** in `ADAPTER`:

```python
"capabilities": {
    "max_batch": 50,          # host chunks to this many items…
    "max_batch_bytes": 200000000,  # …and to this many bytes (default 200 MB)
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
The same mechanism schedules the run that removes photos deleted from Yaffo
(see *Photos deleted from Yaffo*).

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
        known source_id, changed updated_at and changed content → overwrite (see Edited at the source)
        skip + link if hash matches an existing media_items.sha1  (cross-library dedupe)
        tombstone → mark the sync_items row deleted_at_source (never delete the local file; see Source deletions)
        resolve_download if needed → download to temp in the import root
        sniff type (images/videos only), enforce size caps, check free space
        atomic rename to <import root>/<folder by layout>/<sanitized name>
        record watcher suppression (Mechanism 2 in Automations) so file_sync doesn't double-index
        commit this record's media and ledger rows, releasing SQLite's writer lock
    index the page's new files (existing indexer), passing taken_at/location hints
    commit runtime (state_patch, next_cursor) after the page
    cursor = page.next_cursor; until None
promote checkpoint
```

Committing after each record lets the web process cancel a run while the next
file downloads. The cursor advances only after the page, so a crash or cancellation
replays that page; committed ledger rows make its completed records idempotent.
An imported row is marked `index_queued` when its index job is queued, so replay
also queues any file committed just before a crash.
Long backfills run as a resumable task-queue job.

### Target run

```
desired = selection()                            # [{media_item_id, target, caption}]
current = sync_items for this sync
pair up moves: a photo leaving target A and entering target B in the same run
to_update = moves + caption changes              (if capability update; else moves
                                                  become remove + push, captions ignored)
to_push   = desired − current − moves
to_remove = current − desired − moves            (if capability remove; includes rows
            + rows marked removing                 marked by a delete, see Photos deleted from Yaffo)
for each chunk (by count and by bytes) of to_push:
    write intent rows (status = pending, attempt n)      # commit
    res = call push(chunk, ctx)
    commit results (sent | failed[code]) + state_patch in one transaction;
    items with no result (hook-level error) go back to unsent
same for remove / update
```

**Items are keyed by photo, not by (photo, target), when the target has one
destination.** With `targets: "one"` the ledger key is just the
`media_item_id`, so a routing-key change is a no-op instead of a re-upload.
With `targets: "many"` a photo can be in several targets at once, so the key
is `media_item_id:target`, and a photo that leaves one target and enters
another in the same run is treated as a **move** (`update`, or remove + push
without it). Renaming a Yaffo album that's the routing key is therefore an
`update` per photo (or, for adapters that can rename the remote album, one
call), never a re-upload of every photo.

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

### Photos deleted from Yaffo

Deleting a photo removes it from every target it was sent to (see
*Authorization and consent*), but the remote calls don't run inside the
delete. A delete is a local action that must be instant and always succeed:
it can't wait on, or fail because of, a slow or unreachable service; bulk
deletes (the duplicate cleaner, an automation) would otherwise make hundreds
of one-by-one calls; and a removal fired from the delete path would bypass
the one-run-per-sync lock. So the work is **queued before the photo leaves the
database, and performed by the sync run afterwards**:

1. **In the delete's own transaction**, every target `sync_items` row for the
   photo that was sent (or is `pending`) is set to `removing`. Then the
   delete commits, and the rows' `media_item_id` becomes NULL; they keep
   `remote_id` and `target`. This is done once, in the media repository's
   delete function, so every delete path (the UI, the duplicate cleaner,
   `delete_media_items` from automations and assistant plans) gets it.
2. **After commit**, a `media_deleted` event is emitted with the photo ids and
   the ids of the syncs that had rows marked (`sync_automation_ids`). Every
   target sync owns an event trigger for it, created with the sync, whose
   matcher only accepts events naming that sync; it goes through the settle
   window like `album_changed` (see *Coalescing bursts*), so deleting 300
   photos leads to one run per affected sync, not 300. `media_deleted` joins
   the general event catalog, so other automations can subscribe too.
3. **The sync run** treats `removing` rows as part of `to_remove`: it calls
   `remove` in batches with their `remote_id`s, each item labelled with its
   row id as `key`, and records each outcome. Removal is idempotent: a
   `not_found` result counts as removed. A failure leaves the row `removing`,
   and the next run retries it. Once removed, a row with no photo is
   deleted from the ledger; it has nothing left to describe.

If the target can't remove (`remove: False`), the rows are marked
`cant_remove` and the run says so ("12 photos deleted from Yaffo are still in
this target"). The Folder target deletes the copies it wrote, under its usual
rule (only unchanged files Yaffo wrote, to the OS trash).

### Edited at the source

When a record Yaffo already imported comes back with a new `updated_at` and
different content (hash, or size when there's no hash), the engine **replaces
the library's copy** rather than importing a second file. To most people an
edited photo (cropped, filtered, rotated in the provider's app) is the same
photo, and a second copy would just be a duplicate to clean up.

- The new file is downloaded to a temporary name and atomically replaces the
  file at the media item's **current** path (the user may have moved or
  renamed it since import). The previous version goes to the **OS trash**,
  so a bad edit at the source is recoverable.
- The media item keeps its id, so tags, labels, favorites, and album
  membership stay.
- The item is re-indexed (sha1, dimensions, embedded dates, thumbnails, face
  detection), and a `media_modified` event fires, so *Export photo tag* writes
  Yaffo's people and location tags back into the new file.
- Re-indexing re-detects the faces (the edit may have cropped them), so face
  assignments on that photo are made again: by *Auto-assign faces* if it's
  on, otherwise by hand.
- Same content with only a new `updated_at` (a metadata touch at the source)
  just updates the ledger; nothing is downloaded.
- The Folder source follows the same rule when a file's size or modification
  time changes and its content hash differs.

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

- The sync's Items panel lists them under the *Deleted at source* status, so
  they can review those photos and delete them in Yaffo with the normal
  delete (which sends files to the OS trash) if they want to.
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
- `results` must reference only `key`s from the batch it was given, each at most
  once;
- `state_patch` size-capped (64 KiB per sync, JSON only).

## Test / preview

| | Source | Target |
|---|---|---|
| Web reads | **live**, page after page until the listing ends or 5 s pass (`GET`s and POST-as-read endpoints the adapter has granted, marked `read` risk) | live `list_remote` first page, if granted |
| Web writes | n/a | recorded, not sent (`$ref:N` tokens for returned ids, as today) |
| Shows | every record listed as a table (filename, taken, size, hash, "already in library?") plus validation errors | the diff ("would add 12 to *For Mom*, remove 3"), then the adapter's recorded web calls per batch |
| Library writes | none, no downloads | none |

As built, the result appears in a *What a run would do now* panel: when it was
checked ("a preview; nothing was copied or changed"), number tiles for what would
happen (to copy, to remove, already there, converted, can't be sent; for sources,
new, edited, already in the library, already imported, deleted at the source), a
sentence when there's nothing to do, and a paginated table with each file's
planned action. The table uses the shared pagination controls; changing pages
reruns the read-only Test. A source's Test lists one page at a time until the
listing ends or `SOURCE_LIST_BUDGET_SECONDS` (5 s) pass, since the request runs on
the web server's small thread pool, and counts what a run would do with every
record it saw. When it stops early it says how many it counted, of the source's
total when the source reports one (`progress.total`). A web source reads with read
grants only; nothing is downloaded.

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
2. **Sync approval**: what the engine will do with it. For the built-in Folder
   adapters, the user reviews the folder, selection, import root, and removal
   setting in the setup panels, then clicks **Enable**. That action stores the
   consent hash; there is no separate review panel. A web adapter's **Enable**
   opens a review first: where from, which account, into where and how arranged,
   what happens to deletions, and the hosts Yaffo talks to; confirming stores the
   same hash.
   - Source: "Import new photos from *OneDrive › Camera Roll* into
     *Pictures › Imports › OneDrive*." The import root is part of the consent;
     changing it re-asks.
   - Target: "Send *the photos matching this rule* to *Google Photos › For Mom*."
     **Every new version of the selection re-asks**: any change to its kind,
     to a query, to which album is selected, or a newly published selector
     script. There's no attempt to work out whether a change widens or narrows
     the selection. Changes to the *members* of a selected album don't re-ask
     (the album is the user's consent, item by item).
   - **Deleting a photo from Yaffo removes it from every target it was sent
     to** (where the target supports removal), the same as removing it from
     the selected album. A web target's enable review should say so: "Photos
     removed from the album, or deleted from Yaffo, are removed from the Google
     album." For the Folder target, the removal option says whether to remove
     an unchanged copy from the destination or keep it there.
   - Endpoints marked `shares_externally` (OneDrive `createLink`, Immich shared
     links) keep their own separate confirmation, as in the web-calls doc.

## UI

A sync is a **type of automation**, next to system automations and custom
(script) automations. It lives in the existing Utilities › Automations list,
uses the existing detail page, trigger editor, and run history, and adds no
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

After the service, setup is one page, filled top to bottom. The Folder steps
are collapsible panels; completed steps fold to their headings and summaries.
An unfinished sync cannot be enabled until its required setup is complete.
The later account step will also surface `needs_sign_in`:

1. **Account.** If a connection for this provider exists, it's preselected
   ("OneDrive · jason@outlook.com ✓ · Use a different account"), and if this
   sync needs scopes it lacks, the step asks to extend it. Otherwise *Sign in
   to OneDrive* runs the OAuth flow in the browser (API-key providers show a
   key field, Immich also asks for its server URL). Afterwards the section
   shows the account label, "also used by 2 other syncs" when shared, and a
   *Sign in again* link.
2. **Folder and options** for the built-in Folder adapters; other adapters use
   their own option labels. A form generated from `ADAPTER["options"]` (see *Options*):
   plain fields use the macro system automations use for `config_fields` today
   (`render_configure_form`); `remote_folder` / `remote_collection` options
   open a folder browser or an album list on the signed-in account. Locked
   until the *Account* step is done.
3. **What to send / Where to put them.**
   - Source: the import root, using the existing media-dir picker plus a
     subfolder (default `Imports/<sync name>`), and **Arrange by**: keep the
     source's folders (the default), by year and month taken, by year taken, or
     all in one folder. Changing it re-asks; photos already imported stay put.
   - Target: the selection: *Album* (album picker), *Photos matching a filter*
     (the existing filter UI, saved as the `data_query`), or *Custom rule*
     (opens the builder chat on this automation for a selector script).
4. **When.** Defaults filled in, editable in the collapsible trigger panel:
   nightly for sources; for album targets, *when the album changes* plus
   nightly.

**Enable / Disable** is a header action, like on other automation pages. For
the built-in Folder adapters, enabling the completed setup stores the consent
hash; web adapters show the review described under *Authorization and consent*
first. Changing the account, folder, options, import root, or selection disables
the sync until it is enabled again. The header also has **Test**, whose result
appears in a *What a run would do now* panel above Run history.

### The detail page of a sync

Same layout as other automations, with the code section replaced by:

- **Header:** the automation name and a short description of the source or
  selection. Enable / Disable, Test, and Run now are header actions. The On
  badge is in the automation sidebar, not beside the page title.
- **Attention message:** shown below the header when the sync needs action,
  such as reconnecting a missing folder or account.
- **Setup sections:** Folder and options, What to send / Where to put them,
  and When use the same collapsible panel with a left-side caret. Options
  explanations appear in info hovers beside their labels.
- **Run history:** the existing component; each run's summary carries the
  engine's counts ("+34 imported, 2 skipped as duplicates, 12 deleted in
  OneDrive and kept").
- **Items:** a collapsible panel below Run history, folded to its counts ("1
  failed · 2 imported") unless something failed. It holds a search box (file
  names only), a chip per status that occurs (in a fixed order per direction,
  problems first; the selected one stays, even at 0), the ledger as a table
  (photo, status and why, where it is in the library or where it was sent,
  updated), *Retry failed*, and the shared pagination component, updated out of
  band so it isn't redrawn with the table. Paging, search, and counts are
  queries; only one page is ever rendered.

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
- **Filters:** *Imported by* (a sync automation), *Sync failed*.

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
validated statically and shown in the future enable review without running code:

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
- the hosts are listed in plain words in the enable review ("This service talks to
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
                # partial results are kept; this target's items come back as unsent
                return {"results": results, "error": {"code": "retry_later"},
                        "state_patch": {"albums": albums}}
            albums[target] = r["json"]["id"]
        new_items, pending = [], []
        for item in items:
            up = web_upload_media(CONNECTION, "POST", API + "/uploads", item["media_item_id"],
                                  headers={"X-Goog-Upload-Protocol": "raw"})
            if up["status"] != 200:
                results.append({"key": item["key"], "error": {"code": "retry_later"}})
                continue
            new_items.append({"description": item["caption"] or "",
                              "simpleMediaItem": {"uploadToken": up["text"],
                                                  "fileName": item["filename"]}})
            pending.append(item["key"])
        r = web_request(CONNECTION, "POST", API + "/mediaItems:batchCreate",
                        json={"albumId": albums[target], "newMediaItems": new_items})
        for key, res in zip(pending, r["json"].get("newMediaItemResults", [])):
            if "mediaItem" in res:
                results.append({"key": key, "remote_id": res["mediaItem"]["id"]})
            else:
                results.append({"key": key, "error": {"code": "rejected",
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
                                 "help": "Leave empty to use the Yaffo album's name."}},
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
| **Paired device** | another Yaffo, reached through this device's P2P engine, not web calls ([Peer sync](#peer-sync-importing-from-paired-devices)) |

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
  `hash`, so files already in the library are linked, not copied, and an
  edited file replaces the library's copy (see *Edited at the source*). No date or
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

### Paired device (source)

*Import from… › Paired device* copies new photos from a folder or album another
Yaffo shares with this one, everything in it or only what matches a filter. It's
a built-in handler (`peer_source`) over an internal API to the web process's P2P
engine. The design is in [Peer sync](#peer-sync-importing-from-paired-devices).

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
| When a photo leaves the selection | remove from destination · keep at destination | remove from destination |

For a custom selector rule, `target` is the name of the subfolder within Folder.
The Layout choice is hidden and stored as `per_target` so flat/date layouts cannot
discard the rule's routing. The folder, conversion, and removal options still apply.
The Test preview shows each planned full path; an occupied filename may receive a
numbered suffix when the copy is actually made.

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
"written to folder", never "sent". The setup shows the destination and a
*When a photo leaves the selection* choice: **Remove from destination** or
**Keep at destination**. Its info hover explains that Remove sends only an
unchanged copy Yaffo made to the system Trash. The original in the library is
not removed. Clicking Enable records the current setup's consent hash.
Yaffo does not promise what a desktop app does downstream: Amazon's folder
backup only uploads, while deleting from a Dropbox folder may propagate to
Dropbox.

## Targets considered and not planned

| target | why not | what covers it instead |
|---|---|---|
| **Email** (send photos to an address, e.g. a photo frame's) | email isn't an HTTP API, so it can't be an adapter: it would need a built-in target (see above) (Gmail `gmail.send` / Graph `Mail.Send` over OAuth, or SMTP with an app password) plus an `smtp` connection kind for that target only. Sent mail can't be removed (send-only destinations are out of scope, see *Target capabilities*), and frames and personal mail accounts have per-email and daily limits. The audience is small: owners of email-enabled frames (Skylight, Pix-Star, Nixplay) | **deferred.** Revisit if users ask; `accepts` (above) already covers the format half |
| **Amazon Photos** | no public upload API: Amazon closed its Cloud Drive API to new developers years ago and shut Amazon Drive down at the end of 2023 (as of this writing). The only ways in are Amazon's own apps | the **Folder** target, pointed at a folder the Amazon Photos desktop app backs up |
| **Frameo frames** | no API and no email; photos arrive only from the Frameo phone app, peer to peer | the **Folder** target writing to a USB stick or SD card the frame reads |
| **Photo frames generally** | most consumer frames have no public API | frames and smart displays that show a **cloud album** (Google Nest Hub, Nixplay and some Aura models with Google Photos) are covered by the Google Photos target: send an album there and point the frame at it. Frames that read a USB stick or SD card are covered by the **Folder** target |

## Peer sync: importing from paired devices

Built as [Phase 4](#phase-4-peer-sync-from-paired-devices). A **peer sync** is a
sync **source** whose other side is a paired Yaffo device instead of a cloud
service or a local folder: "Copy new photos from *Mom's laptop › Vacation 2026*
into *Pictures › Imports › Mom*", on a schedule or when that device comes online.
It builds on the engine above and on [P2P Device Sharing](p2p-sharing.md)
(pairing, share grants, the wire protocol, `TransferManager`); this section covers
only what's new.

Sending to a device is out of scope. It is not library replication (two-way sync,
conflict resolution, people and album mirroring). [P2P Device
Sharing](p2p-sharing.md#decisions-taken-deliberately) lists that as a separate,
larger design.

### Why it fits the sync engine

Almost every concept of a sync has a direct P2P counterpart, and most of the code
already exists on both sides:

| sync concept | for a peer | already exists |
|---|---|---|
| Provider | a paired Yaffo device | `known_devices` |
| Connection (account) | the trusted `known_devices` row. No OAuth, no token | pairing |
| Adapter | a built-in source handler, `peer_source` (Python, like the Folder source) | `SourceHandler` |
| Option: what to import | one scope the peer has granted: a media dir, folder, or album | `list_shared` |
| Listing (`list_page`) | pages of `list_files` manifests | `ListFilesEndpoint` |
| Download (`fetch`) | a chunked `pull_file` with resume and checksum | `TransferManager` |
| Authorization | the **serving** device's grant, decided per request by `granted_media_query` | yes |
| Consent | Enable on the **importing** device, like any sync | yes |
| Dedupe, layout, ledger, indexing | unchanged | sync engine |

### Process model: the P2P engine stays in the web process

Sync runs execute in taskq workers. The P2P engine runs as an asyncio loop on a
daemon thread inside the web process, and it has to stay there. A second process
running the engine with the same device identity breaks presence and calls:

- **The hub keeps one socket per device.** When a second socket authenticates as
  the same device, the hub closes the first with `CLOSE_REPLACED`
  (`hub/yaffo_hub/hub.py`). The device side treats any close as a lost connection
  and reconnects after 2 s (`yaffo/p2p/signaling.py`), which replaces the other
  process in turn. The two processes keep replacing each other for as long as both
  run.
- **Hub messages go to whichever socket holds the slot.** A `connect_response` for
  a call the worker started can reach the web process, which drops it, so the
  worker's call times out. A peer's request to browse this device can land on the
  worker, which doesn't serve it. To peers, the device appears to go offline at
  random.
- **The hub's connect limit is shared.** It allows 30 connects per minute per IP
  (`hub/yaffo_hub/config.py`). Two processes reconnecting every 2 s come close to
  that, and going over it (`CLOSE_RATE_LIMITED`) locks out the web process too.
- **The UDP port and the mDNS name are single-owner.** A second bind of
  `YAFFO_P2P_PORT` fails, and two processes advertising the same mDNS service
  either collide or get renamed.
- **The scale is larger than one extra process.** taskq runs four workers and
  recycles each one every 100 tasks.

The keypair itself isn't the problem: any process can present the device
certificate, and peers pin it. The problem is ownership of the hub socket, the
UDP port, and the mDNS name.

**Decision:** sync workers never run the engine. They call a small **internal REST
API** on the web process, and the web process makes every peer call through its
single engine.

| alternative | why not |
|---|---|
| A local socket or named pipe (`multiprocessing.connection`) | Isolated from browsers by construction, but it's a second channel to build, test, and debug. REST with loopback, `Host`, and custom-header checks (below) gets the same isolation from browsers, and route tests and `curl` work as they do for the rest of the app |
| Inherited pipes | The web process starts the taskq host with `Popen`, and the host spawns the workers. In dev (`invoke app-local`) web and host are siblings, so there is nothing to inherit from |
| A LAN-only engine in the worker (no hub socket, ephemeral port, no mDNS advertising) | Conflict-free, but only works when both devices are on the same network |
| Several sockets per device on the hub, with replies routed by call token | A hub protocol change and a redeploy, so that workers can do something the web process already does |

### Internal P2P API

#### Transport and security

The API serves local Yaffo processes only. It moves no file bytes; the web process
writes downloaded files itself (see *Pulls*).

- **Blueprint:** `/internal/p2p/v1/…`, JSON in and out. It's kept out of `/api/`,
  out of the navigation, and out of the assistant's `app_pages` (add the routes to
  `NOT_PAGES`).
- **No secret.** The web UI has no login, so there's no user to authenticate. What
  the checks below have to tell apart is *a Yaffo process on this machine* from
  *a web page open in the user's browser*, and from other machines. Any site the
  user visits can make the browser send requests to `127.0.0.1:<port>`, and they
  arrive from loopback. Three checks, all required, answer `403` otherwise:
  - **Loopback caller.** `remote_addr` is `127.0.0.1` or `::1`, even when the
    server is bound more widely (`YAFFO_WEB_HOST` in containers, the docs-capture
    sandbox on `0.0.0.0`). This keeps out other machines.
  - **Loopback `Host`.** The `Host` header is `127.0.0.1:<port>`,
    `localhost:<port>`, or `[::1]:<port>`. This defeats DNS rebinding: a page on
    `evil.example` whose DNS now points at `127.0.0.1` is same-origin with the
    server as far as the browser is concerned, so it could set headers and read
    responses, but its requests still carry `Host: evil.example:<port>`.
  - **A custom header**, `X-Yaffo-Internal: 1`. A cross-site page can't add a
    custom header without a CORS preflight, and the routes never answer one, so
    plain cross-site form posts (CSRF) are refused.

  A secret token was considered and dropped. Its only extra protection is against
  other programs or OS users on the same machine, and those can already use the
  anonymous UI routes, including the remote gallery's own pull endpoint. Guarding
  only this API with a secret would add a token file, rotation, and per-platform
  file permissions without closing anything the UI leaves open.
- **Discovery.** The web heartbeat (`yaffo/process_status.py`) publishes
  `extra.internal_api = {"port": 5001, "version": 1}` through `register_web_extra`.
  A worker reads the port from `web_status.json` and calls
  `http://127.0.0.1:<port>`.
- **No long requests.** waitress has a small thread pool that the UI shares (four
  threads in the demo container). Every call returns within about 10 s, the
  timeout `P2PService._submit` already uses. Anything longer is started, then
  polled (*Pulls*).
- **Demo mode:** pulls are held to the same caps as remote-gallery batches
  (`DEMO_TRANSFER_MAX_FILES`, `DEMO_TRANSFER_MAX_BATCH_BYTES`,
  `DEMO_DOWNLOAD_VOLUME_BYTES` in `yaffo/p2p/transfers.py`), counted per session.

#### Errors

Every error is `{"error": {"code": "...", "message": "..."}}` with a matching HTTP
status. Codes are stable and map onto sync engine behavior:

| code | HTTP | meaning | peer source does |
|---|---|---|---|
| `engine_unavailable` | 503 | P2P is disabled or failed to start | `source_problem` → run ends "sharing is off" |
| `peer_offline` | 409 | no LAN candidate and the hub can't reach the device | `source_problem` → run ends "waiting for the device" |
| `relay_not_allowed` | 409 | only a relayed path exists and the session forbids relay | run ends "waiting for a direct connection" |
| `relay_budget_reached` | 409 | the session used its relay budget | stop at a page boundary; `retry_later` |
| `not_trusted` | 403 | unknown or revoked device | pause, `device_revoked` |
| `scope_not_shared` | 404 | the peer no longer grants this scope | pause, `option_missing` |
| `file_refused` | 404 | the peer refused one file (it left the grant) | that item: `skipped` |
| `source_changed` | 409 | the file kept changing during the pull | that item: `retry_later` |
| `checksum_mismatch` | 502 | the whole-file sha256 didn't match, even after a restart | that item: `retry_later` |
| `bad_destination` | 400 | the destination path failed validation (*Pulls*) | `fatal` (an engine bug) |
| `session_expired` | 404 | an unknown or idle-expired session id | open a new session and continue |

A refused connection to the web process itself (the app is restarting) counts as
`peer_offline` on the worker side: the run ends quietly and the next run retries.

#### Resources

##### Status

```
GET /internal/p2p/v1/status
→ {"available": true, "device_id": "LRSU-BYMN-…", "hub_connected": true}
```

From `P2PService.identity` and `hub_connected`.

##### Devices

```
GET /internal/p2p/v1/devices
→ {"devices": [{"device_id": "…", "display_name": "Mom's laptop",
                "trust_state": "trusted", "online": true, "local": false,
                "last_seen_at": "2026-10-10T18:02:11Z"}]}
```

From `p2p_repository.list_known_devices`, `connected_device_ids()` (`online`, or
`null` when the hub is unreachable, because unknown isn't the same as offline)
and `local_device_ids()` (`local`, found on the LAN by mDNS). The sync UI uses this
for its device picker, and the run uses it as a cheap pre-check before opening a
session.

##### Scopes

```
GET /internal/p2p/v1/devices/{device_id}/scopes
→ {"protocol": 2, "scopes": [
     {"scope_key": "album:12", "scope_type": "album", "album_id": 12,
      "media_dir_id": null, "relative_path": null, "name": "For Mom", "file_count": 84},
     {"scope_key": "folder:6f1c…:Vacation 2026", "scope_type": "folder", …}]}
```

This passes `list_shared.send` through and adds `scope_key`, the string a sync
stores as its option id. `scope_key` is deterministic (`album:<id>`,
`media_dir:<guid>`, `folder:<guid>:<path>`), so it stays valid as long as the grant
exists. `protocol` is the peer's protocol version (see *Wire protocol changes*).

##### Files

```
GET /internal/p2p/v1/devices/{device_id}/files?scope=album:12&cursor=200&limit=200[&session=…][&filters=…]
→ {"files": [{"media_item_id": 9133, "source_id": "6f1c…:2024/July/IMG_1234.HEIC",
              "media_dir_id": "6f1c…", "relative_path": "2024/July/IMG_1234.HEIC",
              "name": "IMG_1234.HEIC", "size": 2311042, "mtime": 1720137730.0,
              "media_type": "image", "date_taken": "2024-07-04T18:22:10",
              "sha1": "…" }],
   "next_cursor": "400", "total": 1342}
```

This is one `list_files` page. It adds `source_id`, the **stable** key the ledger
stores (`media_dir_id:relative_path`). `media_item_id` is only a pull handle for
this listing, because the peer can reuse ids after re-indexing. The cursor is
opaque to the caller; today it's the offset. With `session`, the page is fetched
over that session's open connection instead of a new interactive call.

`filters` is a JSON object of sharing wire filters
(`media_filter_params.wire_filters`): a sync's *What to import* subset. The API
checks it (`invalid_request` otherwise) and the serving device applies it after
scoping to the grant, as it does for the remote gallery. Without it, the page
lists the whole scope.

##### Sessions

A session is one pinned connection to one peer. It goes LAN first, otherwise the
hub relay, with one attempt to upgrade to a direct path. It is the same unit
`TransferManager` uses per batch, with the same reasoning: setting up a connection
is the expensive part, so many pulls share one.

```
POST /internal/p2p/v1/devices/{device_id}/sessions
     {"relay": "never" | "budget", "relay_budget_bytes": 1073741824, "owner": "sync-27"}
→ 201 {"session_id": "s_8f3a…", "path": "lan" | "direct" | "relay", "idle_expires_in": 120}

GET    /internal/p2p/v1/sessions/{session_id}   → {"path": "direct", "relay_bytes": 0, "pulls_active": 1}
DELETE /internal/p2p/v1/sessions/{session_id}   → 204
```

- **Relay policy is chosen per session.** `never`: if the connection doesn't get
  onto LAN or a direct path, opening it fails with `relay_not_allowed` and nothing
  is relayed. `budget`: relay is allowed and throttled as today
  (`RELAY_THROTTLE_BYTES_PER_SECOND`), and pulls stop with
  `relay_budget_reached` at the budget. Nobody is there to click "continue
  anyway" on a scheduled run.
- **Idle expiry.** A session nobody uses for 120 s is closed, so a worker that
  crashed or was recycled doesn't leave a connection open.
- **One session per peer.** Two syncs pulling from the same device share one
  connection (reference-counted by `owner`), so there is still one upgrade attempt
  and one congestion controller.

##### Pulls

A pull downloads one file to a path the caller chooses, and the **web process
writes it**. The worker sends only small messages.

```
POST /internal/p2p/v1/sessions/{session_id}/pulls
     {"media_item_id": 9133, "source_id": "6f1c…:2024/July/IMG_1234.HEIC",
      "expected": {"size": 2311042, "mtime": 1720137730.0},
      "destination": "/Users/…/Pictures/Imports/Mom/.yaffo-27-3b9e….part"}
→ 202 {"pull_id": "p_41c0…"}

GET /internal/p2p/v1/pulls/{pull_id}?wait=10
→ {"state": "queued" | "active" | "done" | "failed" | "cancelled",
   "bytes_done": 1048576, "size": 2311042, "sha256": null, "error": null}

DELETE /internal/p2p/v1/pulls/{pull_id}        → 204 (cancel)
```

- **Long-poll.** `wait` (seconds, at most 10) returns as soon as the state changes,
  or after the timeout. One file needs a handful of requests, not a busy loop.
- **Destination validation** is the security boundary of the write. The path must
  be absolute, its resolved form must be inside a configured media dir, its
  basename must be a sync temp name (`TEMP_PREFIX`…`TEMP_SUFFIX` from
  `yaffo/sync/files.py`), its parent folder must exist, and it must not be a
  symlink. Anything else gets `bad_destination`, so a caller that passes the
  request checks still can't write anywhere else.
- **Resume uses the existing rules.** The pull writes `<destination>.partial` and a
  sidecar with the expected size and mtime, exactly as
  `TransferManager._prepare_resume` does, and renames to `destination` once the
  whole-file sha256 matches. For resume to work across runs, the peer source uses a
  **deterministic** temp name per ledger key (`.yaffo-<sync id>-<sha1(source_id)>.part`)
  instead of a random one, and `sweep_temps` keeps peer partials younger than a
  few days instead of deleting them at run start.
- **Done** returns the sha256, and the engine continues as for any fetched file:
  sha1 dedupe, layout, move into place, index.

#### What stays out of the API

Pairing, grants, revocation, renaming devices, and previews stay UI routes. A
worker never needs them, and keeping them out means nothing that gets past the
request checks can change trust or sharing. The API can read the list of paired
devices and pull from a granted scope, and that's all.

#### The same checks for the whole app

The rest of the app's routes have no `Host` or origin check today. The app-wide
fix (a `Host` allowlist and an origin check on every request that changes
something) is planned separately in
[Web Routes and Request Security](web-routes-and-request-security.md) and doesn't
depend on peer sync.

#### Mapping to the current code

| API | built on |
|---|---|
| `status`, `devices` | `P2PService.identity`, `hub_connected`, `connected_device_ids`, `local_device_ids`; `p2p_repository.list_known_devices` |
| `scopes` | `ListSharedEndpoint.send` |
| `files` | `ListFilesEndpoint.send`, or `build_list_files_request` over a session's `holder.request` |
| `sessions` | `P2PService.open_peer_session`, `_SessionHolder` (reopen on transport failure, `MAX_SESSION_REOPENS`) |
| `pulls` | `TransferManager._download_one`, `_pull_file_once`, `_prepare_resume`, `_chunk_problem`, `_respect_relay_budget`, `_account_relay_bytes` |

**Refactor:** split `TransferManager` in two.

- **`PeerPuller`** holds the session and pulls one file: chunking, resume,
  checksums, restart-on-change, relay accounting and throttling. It reports
  progress through the file entry's state, as today. It doesn't know about
  batches, destination layout, or the UI.
- **`TransferManager`** keeps what the remote gallery needs: batches, manifest
  collection, selection, the `{Device}/{Collection}/{path}` layout, and the
  interactive "continue anyway" relay budget. It uses `PeerPuller` for each file.

The internal API is the second user of `PeerPuller`. Sessions and pulls live in
memory on the engine loop like batches do. A web restart loses them, which is
fine, because the worker sees `session_expired` or a refused connection and the
next run resumes from the partials on disk.

### Peer source (import)

A `sync_adapters` row with `handler = "peer_source"` and `provider = "peer"`,
implemented in Python against the internal API through a small client
(`yaffo/sync/peer_client.py`: base URL discovery, the `X-Yaffo-Internal` header, typed errors).

**Setup**

- **Device step** (in place of *Account*): pick a trusted paired device from
  `GET /devices`. There's no sign-in. Unpaired devices are paired on the Sharing
  page first, and the step links there.
- **Options:**
  - `scope`: a `remote_collection` fed by a Python `choices` that calls
    `GET /devices/{id}/scopes`, stored as `{id: scope_key, label: "Mom's laptop ›
    Vacation 2026"}`. The picker is empty with a hint when the device shares
    nothing with this one. Its label on the page is *Shared folder or album*.
- **What to import:** everything in the share (the default), or only the photos
  matching a filter. The filter is the targets' `query` selection, used by a
  source (`SyncConfig.source_filters`; see *Selection*). It's chosen on its own
  screen (`sync_peer_filter`): the filter panel over the **device's** library,
  its option lists taken from the peer's `list_files` facets scoped to the grant,
  with a match count and live previews. People and label ids are the peer's own,
  so choosing another device or share clears the filter; saving the same share
  again keeps it. Every listing of a run and of Test sends it
  (`HookContext.filters` → `files?filters=`), and the device applies it after the
  grant, so photos it adds later that match are imported too. A file that stops
  matching stays in the library, as one that leaves the share does.
- **Import root and layout** as for every source. *Keep the source's folders* uses
  the manifest's `relative_path`, taken relative to the scope.
- **Consent:** "Copy new photos from *Mom's laptop › Vacation 2026* into
  *Pictures › Imports › Mom*." The device, scope, filter, and import root are
  part of the consent hash. The review lists *Which ones*: all of them, or only
  those matching the filter.

`SyncConfig.connection_key` stays `None` (like the Folder source). The device is an
option (`device: {id: device_id, label: display_name}`), so it's covered by
validation and consent like any option. The stored label is the device's name when
it was chosen; the pages show its current name from `known_devices` instead
(`shown_options`), so renaming a device doesn't change the consent hash and turn
the sync off.

**Records**

| record field | from the manifest |
|---|---|
| `source_id` | `media_dir_id:relative_path`: stable across the peer re-indexing |
| `filename` | `name` |
| `folder` | the `relative_path` folder, relative to the scope |
| `size` | `size` |
| `updated_at` | `f"{size}:{mtime}"`, so a changed file follows *Edited at the source* |
| `hash` | `{"algo": "sha1", "value": sha1}` when the peer sends one (protocol 2) |
| `taken_at` | `{"local": date_taken}` |
| `deleted` | never sent (see below) |

The engine keeps the manifest's `media_item_id` on the record (as Folder keeps
`local_path`) so `fetch` can name the file to pull.

**No tombstones.** A file that leaves the grant (unshared, removed from the album,
deleted on the peer) just stops being listed, as with the Folder source.
Nothing is marked `deleted_at_source` and nothing local is touched. Yaffo can't
tell "deleted" from "no longer shared", and the library is the master copy either
way.

**Run**

1. `source_problem`: `GET /devices`. A revoked or unknown device pauses with
   `device_revoked`. A device that's offline both on the hub and on the LAN ends
   the run normally with "waiting for *Mom's laptop*".
2. A session is opened lazily on the first `fetch` (a run where nothing is new
   never opens one) and closed when the run ends.
3. `list_page` → `GET /files` over the session once it's open.
4. `fetch(record, destination)` → `POST /pulls`, then long-poll until it's done.
   A run that reaches its time budget cancels the in-flight pull and keeps the
   partial.
5. Errors map as in the *Errors* table.

**Throughput.** The source engine fetches one record at a time.
`TransferManager` pulls three files at once on its one connection, and a strictly
sequential source would leave the connection idle between files. The handler
therefore runs **ahead** by up to three files: when the engine asks for file N,
the handler has already started pulls for N+1 and N+2 to their deterministic temp
names. Their results are checked when the engine reaches them. This needs no engine
change; a pull that was started ahead but never reached (the run ends early) is
left as a partial and resumed next run.

**Two devices importing from each other.** Suppose A imports B's folder and B
imports a scope on A that contains A's import root. Without hashes, every photo
crosses twice before dedupe recognizes it. With protocol 2's `sha1` the engine
links them before downloading (`linked_existing`) and no bytes move. On top of
that, the paired device's Sharing page warns when a grant back to that device
contains its peer sync's import root.

### Relay policy for unattended runs

Relay egress is the hub's only metered cost, and an unattended nightly backfill is
exactly the traffic that policy was written to discourage. Batches in the remote
gallery stay as they are (a person is watching and can continue anyway).
Scheduled peer syncs always use `relay: never`. They run on the LAN or after a
successful upgrade to a direct path, and otherwise end with "waiting for a direct
connection". The run summary shows whether LAN or direct was used.

The automation's Run history shows one sync run. Its progress advances after each
committed page of the peer's file listing; import and index jobs queued for those
pages remain background work and do not appear as separate sync runs.

### Triggers

- **Schedule:** nightly by default, like other sources.
- **`device_online`:** a new domain event, emitted by the web process when a paired
  device goes from absent to present, either on the hub (presence polled through
  `connected_device_ids` every few minutes) or on the LAN (an mDNS service-added
  callback). Payload `{"device_id": …, "path": "hub" | "lan"}`. A per-trigger
  `config.device_id` filter works like `album_changed`'s `album_id` filter, and the
  settle window (*Coalescing bursts* above) absorbs a device that flaps.
  Peer syncs own this trigger by default, so a laptop that's opened once a week
  imports when it's opened, not on the first nightly run after.

### Wire protocol changes

1. **A protocol version.** Today's messages carry no version. Add
   `"protocol": 2` to every signed request and response. A missing value means 1.
   Each side adapts to the lower version: a v1 peer just doesn't send `sha1`, and
   the importer then dedupes after downloading. Do this first, because every later
   change depends on it.
2. **`sha1` in manifests**, from the stored `media_items.sha1` (computed at index
   time, backfilled by the existing task). It's `null` when not computed yet. This
   keeps the rule that browsing never hashes file contents.

Deferred: a `modified_since` filter on `list_files`, so incremental runs don't
re-list the whole scope. A full listing of a few thousand files is a handful of
pages; measure before adding it. Binary chunk framing (no base64) also stays
deferred, as in the P2P doc.

### Open questions

- **Album scopes and local albums.** An import of a peer's album could also add the
  imported photos to a local album of the same name. It's local-only and needs no
  people mapping, so it's cheap. Is that wanted by default, or as an option?
- **Revocation mid-run.** The peer re-decides per request, so the next pull fails
  with `file_refused` or `scope_not_shared`. Should the run then pause, or finish
  the page and pause?
- **Several syncs from one device.** Sharing a session per peer is in the design;
  should their runs also be serialized per peer, so two nightly imports don't
  compete for the same connection?

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
    query: dict | None = None         # filter selections in the sharing wire format,
                                      # validated by media_filter_params.validate_wire_filters

@dataclass(frozen=True)
class SyncConfig:                     # automations.sync["config"]
    role: str                         # source | target
    adapter_id: int
    connection_key: str | None        # application_settings name of the connection; None only for
                                      # built-ins with no account (Folder source/target)
    scopes_needed: list[str]          # what this sync needs; must be ⊆ the connection's scopes
    options: dict                     # validated against the adapter's ADAPTER["options"]
    import_root: dict | None          # sources: {"media_dir_id": uuid, "subpath": "Imports/OneDrive",
                                      #           "layout": "source_folders"}
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
    pause_code: str | None            # why it's paused: selection_missing | destination_missing |
                                      # option_missing
    last_run_at: str | None
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
              targets with targets: "one" → f"{media_item_id}";
              targets with targets: "many" → f"{media_item_id}:{target}".
    media_item_id goes NULL if the local photo is deleted. For a source that must
    not cause a re-import; for a target the row was set to 'removing' in the same
    transaction, and the next sync run removes it remotely using remote_id (see
    Photos deleted from Yaffo). Hook items and results identify the row by its
    id (`key`), not by item_key or media_item_id."""

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
                                                                   # target: pending | sent | failed |
                                                                   #   removing (queued or in flight) | removed |
                                                                   #   removed_remotely | cant_remove
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
album title, Flickr's "make public"). Adapter grants are approved at publish;
the sync setup is approved at Enable. This check at run start is a backstop.

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

## Implementation phases

Built-in and preset sources and targets come first, custom adapters last. Two
sequencing choices inside that:

- **Local before cloud.** The Folder source and target need no sign-in, no web
  calls, and no Starlark hooks, but they exercise almost the whole engine:
  sync automations, the item ledger, diffing, crash recovery, consent, the
  `album_changed` trigger, `accepts` conversion, and the setup UI. Building
  them first proves the engine and UI with no external dependencies, and
  ships something useful on its own (Amazon Photos via folder backup, Dropbox
  via its desktop folder, SD cards for frames and from cameras).
- **Read-only cloud before writing to the cloud.** The first cloud preset is a
  source (OneDrive): it can't damage anything remotely, and it exercises
  sign-in, hooks, paging, delta cursors, resume, and downloads. Writing
  (uploads, reconcile, the intent log against a real service) comes next.

Presets are written as **Starlark adapters**, not Python, even though we
write them: they're the first users of the hook contract, so the contract is
proven by real providers before the builder starts generating adapters
against it.

Phase 5 includes the user guide, translations, and generated Playwright tests
for the sync flows built in Phases 1–3 and 5, after those flows settle. Phase 4
(peer sync) carries its own, using the two-instance sharing sandbox.

### Phase 0: lead-time items (start now, in parallel)

- **OAuth app registrations** for the presets: Microsoft Entra app (OneDrive)
  and a Google Cloud project with the Photos Library API. Both are
  desktop/public clients (PKCE, loopback return address on the bare root
  path).
  - **Done 2026-10-09.** Both pass a live sign-in check, and the docs moved
    to `yaffo.app` (home page, privacy policy, and Microsoft's
    publisher-domain file).
  - Google stays in Testing (listed test users, 7-day tokens) until the
    verification submission, which moved to Phase 3's exit.
  Steps, acceptance criteria, and the `scripts/oauth_probe.py` check are in
  [OAuth App Registrations](oauth-app-registrations.md).

### Phase 1: sync engine + Folder target and source

- Data model: `automations.sync`, `sync_items`, `sync_adapters` (built-in
  rows with `handler`), `media_items.sha1` with its backfill task.
- Engine: target run (diff, chunking, intent rows, crash recovery, removals
  limited to Yaffo's own copies), source run (records, dedupe by sha1, atomic
  writes into the import root, watcher suppression, indexing), per-sync run
  lock, `accepts` JPEG conversion.
- Triggers: `album_changed` event and emit sites, the per-trigger `album_id`
  filter, and the settle window on the dispatcher tick; `media_deleted` with
  the delete-transaction marking of ledger rows.
- UI: *New automation* type choice (Import from… / Send to…), the sync detail
  page (collapsible setup panels, Enable / Disable header action, Run history,
  Items panel), selection by **album** and **filter** (`data_query`), consent
  hash stored on Enable, and a Test preview table.
- **Exit:** "keep my *For Mom* album copied to a folder, as JPEG" and "copy
  new photos from this SD card into Imports/Camera" work end to end and
  survive a crash mid-run.

**Status: built (2026-10-10).** The exit criteria pass end to end: the real app,
queue host and watcher, driven over HTTP, with the queue host and its workers
SIGKILLed mid-batch and restarted. Implementation notes, where it settled
details this doc left open:

- **Code:**
  - `yaffo/sync/`: schemas, setup, engine with `target_run` and `source_run`,
    built-in handlers, conversion, guards, preview, deletes;
  - `background_tasks/tasks/sync_run.py`: the run task;
  - `background_tasks/event_filters.py`: trigger filters and the settle window;
  - `routes/utilities/sync_views.py` and `sync_text.py`: pages and codes → words;
  - migration `016`.
- **Handlers:** the engine talks to `SourceHandler` / `TargetHandler`
  (`yaffo/sync/handlers.py`), with dict envelopes shaped like the hook
  contract. Built-ins implement them in Python. Phase 2 wraps Starlark hooks in
  the same interfaces.
  - Built-in batch items also carry the file's `path`, and built-in results may
    carry `details`.
  - A built-in target answers `reconcile(items)` with present or missing per
    ledger row, instead of `list_remote`.
- **Run lock:**
  - One run per sync, via a queue lock named per automation
    (`Task.enqueue_locked`).
  - The scheduler tick leaves a due deferred trigger alone while that sync has
    a RUNNING run Job.
- **Crash safety:**
  - Temporary files are named per sync (`.yaffo-<sync id>-….part`), and each
    run sweeps its own sync's leftovers first.
  - Copies reuse an identical file already at the destination name, so a
    retried batch never makes `IMG (2).jpg`.
- **Deletes:** both row-deleting paths (`media_repository.delete_media_items`
  and `index_photos.delete_orphaned_media_items`, used by the watcher, file
  sync and the duplicate cleaner) mark rows `removing` and announce
  `media_deleted`. SQLite foreign keys are off, so the ledger's
  `media_item_id` is cleared explicitly.
- **`album_changed`:** bulk operations (add all matching, remove all) don't
  list ids; they set `truncated: true`.
- **Options:** built-ins use a host-native `local_folder` option type (the
  in-app folder picker; the value is an absolute path).
- **Folder target `keep` option:** removals succeed without deleting (`kept`),
  so the copy stays and the run doesn't nag.
- **Hashes:** `media_items.sha1` is computed at index time. A source run
  hashes un-hashed library photos first, within a third of its time budget.


### Phase 2: web foundations + OneDrive source

- Connections: `sync_connection:` rows in `application_settings`, keychain
  storage, the shared-account rules (scope union, shared status, refresh
  lease, garbage collection), the *Account* step with an existing account
  preselected.
- Sign-in: `oauth2` with the loopback return address and PKCE; device code
  as the fallback link.
- Adapters: the `adapter` host profile, hook invocation (one evaluator per
  call), contract validation, preset seeding with code hash and manifest,
  the `web_request` host function with grant checks and the follow-up-URL
  rule, the static endpoint detector, and the `adapter` time limits (web time
  excluded from the deadline, byte-based batch chunking).
- Options: `remote_folder` / `remote_collection` with the `choices` hook and
  the picker UI.
- Preset: **OneDrive source** (delta feed, backfill + incremental, resume,
  tombstones as `deleted_at_source`).
- **Exit:** a 10,000-photo OneDrive backfill completes across several runs and
  restarts, and later runs import only new photos.

**Status: built (2026-10-10).** The exit criterion passes against a fake
Microsoft identity platform and Graph (`scripts/fake_onedrive.py`, also usable as
the UI sandbox's OneDrive): 10,000 photos import across several runs, each in a
fresh session, and a later run imports only the new ones. Against a real personal
OneDrive: browser and device-code sign-in, the folder picker, a backfill with
folder layout, retries of failed downloads, and an incremental run. Implementation
notes, where they settle what the doc left open:

- **Code:** in `yaffo/sync/`:
  - `connections.py`, `secrets_store.py`, `oauth.py`, `pending_sign_ins.py`:
    accounts, keychain, sign-in;
  - `providers.py`, `oauth_clients.py`: provider presets and Yaffo's app ids;
  - `adapter_static.py`: the static check and endpoint detector;
  - `web.py`: `web_request`, `url_template`, downloads;
  - `starlark_adapter.py`, `access.py`: hooks and the adapter profile;
  - `preset_seed.py`, `presets/onedrive_source.star`: presets.
  
  Routes: `routes/utilities/sync_account.py`. Migrations `017` (ledger sort
  index) and `018` (import layout).
- **Consent covers the grant set, not every byte of code.** A Starlark adapter's
  part of the consent hash is a hash of its provider, scopes, and grants, so a
  preset fix that doesn't widen what it may call leaves syncs on; the grants
  themselves stay pinned to the code hash.
- **Grants match method, scheme, host, and path**; query strings aren't part of a
  grant.
- **Follow-up URLs** (nextLink, deltaLink) are allowed only when a granted call
  returned them in this run, or when the engine stored them as the cursor or
  checkpoint after vouching for them (`runtime.followups`, hashes of at most the
  two). Credentials go only to `base_urls`; download hosts never get them.
  OneDrive personal downloads redirect to `my.microsoftpersonalcontent.com`.
- **Scopes** an adapter needs are declared in `ADAPTER["scopes"]`; the provider
  adds its base scopes (`offline_access`, `User.Read`).
- **Account lookup after sign-in** (`whoami`) retries server errors: Graph's
  `/me` sometimes answers 504 right after a good sign-in.
- **Pending sign-ins** survive a web-app restart: a short-lived
  `sync_pending_sign_in:` row in `application_settings`, its secret half (PKCE
  verifier, device code) in the keychain, claimed once, purged when expired.
- **Tokens are refreshed only at a page boundary**, because the refresh lease is
  a database write and a page may hold the write lock; a mid-page 401 fails that
  item and refreshes at the next boundary.
- **Failed downloads are retried** at the start of later runs (up to 5
  attempts) from the record kept on the ledger row, since a delta feed won't
  list them again.
- **Capture hints** (`taken_at`, `location`) apply on a photo's first index only,
  when the file has none of its own.
- **Import layout:** `import_root.layout` (keep the source's folders, by year and
  month, by year, flat). Records carry `folder`; the OneDrive preset derives it
  from parent folder ids (delta responses carry no paths), looking each folder up
  once and caching paths in its state. Syncs created before the choice keep year
  and month (migration `018`, which re-stamps valid consents).
- **The `adapter` profile** is its own small registry (`ADAPTER_HOST_API`), not
  `HOST_API`: its functions are bound to a sync's web access, not a session.
- **Remote options** store exactly `{id, label}`; the label is the picker's
  breadcrumb path.
- The OneDrive target has the same export layouts as the built-in Folder target:
  one subfolder per selection target, all in the selected folder, or folders by
  capture year and month. Layout is shown for album/filter selections; a custom
  rule supplies its own single folder name and hides the choice. The Test preview
  shows the resulting OneDrive destination folder. A layout change re-asks consent
  and queues existing copies for removal and re-copying at the new destination.

### Phase 3: cloud targets

- `web_upload_media` (raw, multipart, and chunked upload sessions).
- Preset: **Google Photos target** (album per routing target, `list_remote`
  reconcile, the album picker limited to Yaffo-created albums), then a
  **OneDrive target** (folder per target, chunked uploads).
- Reconcile pass: weekly drift check, `removed_remotely`.
- Selection by **selector script** (`script` kind), reusing the existing
  automation builder chat with the read-only profile.
- **Exit:** adding photos to *For Mom* shows up in Google Photos within about a
  minute; killing the worker mid-batch and restarting creates no duplicates.
- **Release gate:** Google OAuth verification.
  - Record the demo video (consent screen, then album creation, upload, and
    add/remove).
  - Publish the consent screen, and submit with the scope justifications in
    [OAuth App Registrations](oauth-app-registrations.md#verification).
  - The Google Photos target can't ship to more than test users until
    verification passes.

**Implementation status (2026-10-10):** The host upload function accepts raw
bytes, multipart form uploads, and bounded byte ranges for provider upload
sessions. It reads only prepared batch items; an adapter cannot pass a local
path. Google Photos and OneDrive target presets are seeded and pass static
endpoint checks. The Google preset creates or reuses app-created albums,
uploads bytes, creates media items, removes album membership, and lists its
remote items. The OneDrive preset creates or reuses a child folder per target,
uses chunked upload sessions, and deletes only with the copy's original eTag
(`412` keeps a copy changed remotely). Both use the existing weekly reconcile
path. Cloud filenames include a stable `__yaffo_<ledger id>` suffix before the
extension so a pending intent can be matched to exactly one completed upload
after a worker crash, even when local files share names. The host restores the
remote id and adapter state from that match.

The `script` selection runs the automation's published Starlark with the
read-only host profile, validates up to 5,000 desired records, and offers the
existing builder chat with a selector-specific prompt and Test tool. Its
published code hash is part of the sync consent. Local tests cover the upload
boundary, both target hooks, a simulated lost Google batch result, OneDrive
chunk ranges and changed-copy protection, selector isolation, and the setup
page. The existing sync regression suite passes. The **What to send** panel now
uses one mutually exclusive choice for album, filter, or custom rule, with one
primary action and details for only the selected choice.

A live Google Photos test on 2026-10-10 sent three photos to an app-created
album. The completed job reported `sent=3` and `failed=0`; all three ledger
rows are `sent`, have distinct remote IDs, and have no error code. The user
confirmed the photos appeared in Google Photos. This verifies the basic upload
path. A later read of the real album found all three remote IDs. A recovery
run against a temporary copy of the live database changed one row to `pending`
and cleared the remembered album state; the engine rediscovered the album and
remote item, restored the original remote ID, and made zero upload calls.
Removal now stores the album ID on each sent item and discovers an older
item's album when state is missing. Local tests cover this recovery path,
membership removal, and weekly remote-deletion drift. A process-level test
also exits the worker immediately after the fake service creates the remote
item, then restarts against the same SQLite database and finds the item without
another upload. The full sync regression suite passes (156 tests).

**Still required for the Phase 3 exit:** an actual worker-kill/restart check
mid-batch against the live service, the demo video, and Google's OAuth
verification. The app remains
limited to Google's configured test users until verification passes.

### Phase 4: peer sync from paired devices

Import from another Yaffo this device is paired with, through the same source
engine. The design is in [Peer sync](#peer-sync-importing-from-paired-devices).

1. **Hardening.** Unattended, recurring transfers raise the stakes on the
   [P2P doc's](p2p-sharing.md) outstanding work, so it comes first:
   - the `aioquic` canary test for the private `_peer_certificate` access (the one
     silent failure mode);
   - timeouts retuned against real NATs instead of loopback values;
   - serving-side rate limiting per peer;
   - the two-machine LAN run with `zeroconf` packaged.
2. **Internal API.** The `PeerPuller` / `TransferManager` split, the
   `/internal/p2p/v1` blueprint (loopback caller, `Host`, and header checks;
   discovery through the heartbeat), sessions, and pulls. Tests: route tests
   against a fake service, including each request check, plus the two-instance
   loopback harness driving a session and a resumed pull through the API.
3. **Protocol 2:** the version field and `sha1` in manifests.
4. **Peer source:** handler, device step, scope picker, *What to import* (the whole
   share or a filtered subset), relay option, deterministic temp names and the
   `sweep_temps` change, read-ahead, and the `device_online` event and trigger.
5. User guide, translations, and Playwright specs once the flows settle, using the
   two-instance sharing sandbox.

- **Exit:** a 2,000-photo scope imports across two runs with a restart in between,
  the second run imports only new files, and a mutual import moves no duplicate
  bytes.

**Status: built (2026-10-10); the two-machine checks, the guide, translations, and
browser specs remain.** Between two instances on one machine, a run over the LAN
path imported 2,160 photos (2 more linked by hash) and the next run found all
2,162 unchanged. Implementation notes:

- **Code:**
  - `yaffo/p2p/`: `peer_api.py` (sessions and pulls on the engine loop),
    `puller.py` (`PeerPuller`), `presence.py` (`device_online`), `rate_limits.py`
    (per-peer token buckets, checked after the signature);
  - `yaffo/routes/internal_p2p.py`: the `/internal/p2p/v1` blueprint;
  - `yaffo/sync/peer_client.py`, `peer_source.py`: the client and the handler;
  - migration `019`: the built-in `peer_source` row.
- **Hardening:** the canary test (`tests/yaffo/p2p/test_quic_transport.py`) and
  the serving-side rate limits are in. Retuning timeouts against real NATs and the
  two-machine LAN run remain.
- **What to import** stores its filter as a `query` selection on the source
  (`setup.set_import_filters`), so the existing consent hash covers it. The filter
  screen reopens on the saved filter (`query_params_from_wire`), and its option
  lists come from the device (`remote_filters_context`, shared with the remote
  gallery). The device's tag values work for album shares too.
- **Turning a peer sync on** goes through the same review modal as a web sync
  (`SyncPage.reviews_before_enable`), listing the device and share, which photos,
  the destination, the allowed paths, and what's kept.
- **Indexing:** each committed page queues its own import and index Jobs, which
  run while the download continues; the sync's Run history shows only the sync run.
- **Still required for the Phase 4 exit:** the two-machine LAN run (with the
  2,000-photo exit run across machines and the mutual import), timeouts retuned
  against real NATs, then the user guide, translations, and Playwright specs.

### Phase 5: custom adapters and self-hosted servers

- The *Add a new service…* builder: `write_adapter_code`, `test_adapter`,
  the Overview / Test account / Web access / Code / Test panel, the publish
  modal and grants, republish and *Regenerate*.
- The `connection` block and its sign-in methods: `oauth2`, `api_key` (header,
  query, and path placements, the `check` call), and `basic` first;
  `oauth1a`, `sigv4`, and `session` when real services need them (rule of
  three). App credentials, return-address cases, `app_form_labels`.
- Self-hosted `instance` handling (server address added to allowed hosts and
  consent), including the http-on-LAN decision.
- Preset: **Immich** source and target, the first `api_key` + self-hosted
  preset, built alongside the builder because both need the same sign-in
  pieces.
- Builder web access: `fetch_docs` with the deterministic, per-domain batch
  authorization (`doc_domains`, Public Suffix List snapshot), permission 3
  (live reads with the test connection).
- Finalize the sync UI: write the user guide, translate the UI text, and
  generate and run Playwright tests from
  `yaffo_ui_tests/specs/sync_automations.yaml` and the later-phase UI specs.
- **Exit:** the builder produces a working Flickr target from a one-line
  request, with the user approving only domains and the publish modal; two-way
  Immich sync works against a local Immich in the UI test sandbox; the sync
  guide, translations, and generated Playwright tests are complete.

### Phase 6: later

- `web_search` with a configurable search backend.
- The `volume_mounted` trigger for Folder sources (import when a card is
  inserted).
- The deferred email target, if users ask.

### Not planned

- **Two-way sync.** An *Import from* and a *Send to* on the same place don't make
  a mirror, by design: the library is the master copy, so deletions at the source
  never delete local photos, edits made in Yaffo aren't sent back, and imports
  carry no albums. Pairing them is allowed but not a supported scenario.

### Contract decisions from the design review

Folded into the sections above: partial results with a hook error (*Common
return envelope*), web time excluded from the 60 s limit plus byte-based
chunking (*Hooks are called by the host*), routing changes as moves (*Target
run*), `cursor_invalid` (*Common return envelope*), overwrite on edit (*Edited
at the source*), results matched on `key`, the ledger row id (*Target
hooks*), and photos deleted from Yaffo removed from their targets, queued in
the delete transaction and done by the next sync run (*Photos deleted from
Yaffo*).
