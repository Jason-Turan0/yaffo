# Sync Automations: Technical Reference

Sync automations move photos between the library and other places: folders,
cloud services, self-hosted servers, and paired Yaffo devices. An *Import from*
sync (a **source**) copies new photos into the library; a *Send to* sync (a
**target**) keeps a selection of the library copied somewhere else.

Responsibility is split three ways:

- **Yaffo's host runs one sync engine** for both directions. It owns diffing,
  per-item state, batching, retries, rate limits, resume, crash recovery,
  downloads, file writes, and indexing.
- **An adapter** owns only the provider-specific part: how to list a provider's
  items (source) or how to push and remove them (target). Web adapters are small
  Starlark modules; built-ins that make no web calls (Folder, Paired device) are
  Python handlers behind the same interface.
- **The selection** decides what a target sends: a Yaffo album, a saved filter,
  or a selector script.

Shipped **presets** (OneDrive, Google Photos, Immich) are adapters written and
reviewed by us, in the same format the AI builder writes for any other service.
This extends the sandbox, host API, and run model in [Automations](automations.md).
Pairing and the wire protocol behind peer sync are in
[P2P Device Sharing](p2p-sharing.md). Yaffo's own OAuth app registrations are in
[OAuth App Registrations](oauth-app-registrations.md).

## Architecture

```
                 ┌──────────────── Connection (account at a provider: auth, base_urls, followup_hosts)
                 │
 Source sync     ▼                         Host sync engine                      Library
 ┌─────────────────────┐  list_page()   ┌──────────────────────────────┐  write  ┌──────────┐
 │ Source adapter      │ ─────────────► │ validate records             │ ──────► │ Imports/ │
 │ or built-in handler │ ◄───────────── │ dedupe (ledger + sha1)       │         │  <src>/  │
 └─────────────────────┘  cursor        │ download → sniff → atomic mv │         └────┬─────┘
                                        │ metadata hints → indexer     │              │ index
                                        └──────────────────────────────┘              ▼
                                                                                 media_items
 Target sync
 ┌─────────────────────┐  select       ┌──────────────────────────────┐  push()   ┌──────────────┐
 │ Selection           │ ────────────► │ diff vs ledger               │ ────────► │ Target       │
 │ album | query |     │  (ids,        │ batch, retry, backoff        │ ◄──────── │ adapter or   │
 │ selector script     │   routing,    │ intent rows + reconcile      │  results  │ built-in     │
 └─────────────────────┘   captions)   └──────────────────────────────┘           └──────┬───────┘
                                                                                         │ web_request /
                                                                                         ▼ web_upload_media
                                                                                     provider API
```

A **sync automation** is an ordinary `automations` row whose `sync` JSON column
is set. It reuses everything automations already have: triggers, runs recorded
as `Job`s, run history, enable and disable, and Test. Dispatch sends an
automation with a `sync` column to the sync engine, alongside system handlers
and custom code.

### Code map

| area | where |
|---|---|
| JSON shapes (`SyncConfig`, `SyncRuntime`, `SyncConnection`, `AdapterVersion`, `WebGrant`, …) | `yaffo/sync/schemas.py` |
| Engine: source and target runs, shared run plumbing | `yaffo/sync/engine.py`, `source_run.py`, `target_run.py`, `run_common.py` |
| Handler interfaces (`SourceHandler`, `TargetHandler`) and built-ins | `yaffo/sync/handlers.py`, `folder_source.py`, `folder_target.py`, `peer_source.py` |
| Loading adapters; Starlark hook invocation and the `adapter` host profile | `yaffo/sync/adapters.py`, `starlark_adapter.py`, `access.py` |
| Static check and endpoint detector | `yaffo/sync/adapter_static.py` |
| Web access: `web_request`, `url_template`, uploads, downloads | `yaffo/sync/web.py` |
| Providers, connections, sign-in, keychain | `yaffo/sync/providers.py`, `oauth_clients.py`, `connections.py`, `credentials.py`, `oauth.py`, `oauth1.py`, `pending_sign_ins.py`, `secrets_store.py` |
| Presets and seeding | `yaffo/sync/presets/*.star`, `preset_seed.py` |
| Selection, consent, setup, options | `yaffo/sync/selection.py`, `consent.py`, `setup.py`, `options.py` |
| Test / preview, builder Test | `yaffo/sync/preview.py`, `adapter_test.py` |
| Format conversion, file writes, guards | `yaffo/sync/convert.py`, `files.py`, `target_layout.py`, `guards.py` |
| Photos deleted from Yaffo | `yaffo/sync/deletes.py` |
| Builder web access (`fetch_docs`, domain rules) | `yaffo/sync/fetch_docs.py`, `doc_domains.py`, `data/public_suffix_list.dat` |
| Run task, event filters, settle window | `yaffo/background_tasks/tasks/sync_run.py`, `event_filters.py` |
| Builder agent and adapter generation task | `yaffo/site_agents/adapter/`, `background_tasks/tasks/generate_adapter.py` |
| Peer sync (web process side) | `yaffo/p2p/peer_api.py`, `puller.py`, `presence.py`, `rate_limits.py`, `routes/internal_p2p.py`; client `yaffo/sync/peer_client.py` |
| Pages and code-to-text | `yaffo/routes/utilities/sync_views.py`, `sync_account.py`, `sync_services.py`, `sync_text.py` |
| Ledger and adapter repositories | `yaffo/db/repositories/sync_repository.py`, `service_builder_repository.py` |
| Migrations | `016`–`021` under `yaffo/scripts/db/migrations/` |
| Fake providers for tests and the UI sandbox | `scripts/fake_onedrive.py`, `fake_immich.py`, `fake_flickr.py` |

## Concepts

The UI says **service** where this doc says *adapter*, since that's what a user
picks. **Provider** always means an external service as Yaffo knows it; the AI
models behind the builder are **LLM providers**.

| object | what it is | where it lives |
|---|---|---|
| **Provider** | an external service: how to sign in, which hosts may be called, default scopes. No sync logic | presets: `ProviderPreset` in `providers.py`; custom: the adapter's `connection` block |
| **Adapter** | the code for **one direction** against **one provider**: hooks, options, capabilities | a `sync_adapters` row |
| **Sync automation** (a *sync*) | one use of an adapter: account, options, destination or selection, triggers, progress | an `automations` row with a `sync` column |
| **Connection** | a signed-in account at a provider, shared by every sync and builder test that uses it | an `application_settings` row; the secret in the OS keychain |
| **Selection** (targets) | what leaves Yaffo: an album, a filter, or a selector script | the sync's config; a script in the automation's code columns |
| **Import root** (sources) | where imported files land, and how they're arranged | the sync's config |
| **Sync items** | the per-item ledger of what was imported or sent | `sync_items` rows |
| **App credentials** (custom OAuth adapters) | the user's own developer-app registration (client id and secret) | the keychain, referenced by `sync_adapters.app_secret_ref` |

Relationships:

- **A provider has at most one preset adapter per direction**, plus any custom
  adapters. OneDrive and Immich have a source and a target; Google Photos has
  only a target.
- **An adapter is exactly one direction.** Import from and export to one service
  are two adapters sharing a provider.
- **An adapter serves many syncs.**
- **A sync has one adapter and at most one connection**, and the connection's
  provider must match the adapter's. Built-ins with no account (Folder source and
  target, Paired device) have no connection.
- **One connection per account, shared.** Whichever sign-in happens first creates
  it (a sync's *Account* step, or the builder's *Test account* tab); later syncs
  on the same provider default to it.
- **A custom adapter is its own provider**, `custom:<adapter slug>`, declared by
  its `connection` block. A custom import and a custom export for the same service
  declare two identical providers; that duplication is accepted rather than
  letting one adapter borrow another's sign-in setup.

## Connections and sign-in

### Shared accounts

A connection is one `application_settings` row named
`sync_connection:<provider>:<account_id>`, of type `json`, holding a
`SyncConnection`. The unique name is what makes "one connection per account"
hold: signing in to the same account again finds the existing row. All access
goes through `yaffo/sync/connections.py`, which validates the JSON on every read
and write. The secret (token, key, or username and password) is in the OS
keychain under `secret_ref`; the database never holds it.

`account_id` is the provider's stable id for the user (Google's `sub`, the
Microsoft account id, Flickr's `nsid`, Immich's user id), from the sign-in's
`check` call or token response. For `basic` it's the username plus the server.
Where nothing better exists, it's a fingerprint of the key and instance.

Connections are shared rather than per sync because many OAuth 2 services
**rotate refresh tokens**: two copies of one sign-in would sign each other out.
Sharing has these consequences:

- **Scopes are the union** of what the syncs using a connection need. A sync that
  needs more asks in its *Account* step and re-runs the sign-in for the union.
- **Status is shared.** An `auth` failure marks the connection `needs_sign_in`,
  and every sync using it shows *Sign in again*; one sign-in fixes them all.
- **Token refresh is serialized per connection** without holding a write lock
  across the network call: a short conditional `UPDATE` takes a lease
  (`refreshing_until`), the holder refreshes and writes the keychain, and a
  concurrent run that finds the lease waits and re-reads. Tokens are refreshed
  only at page boundaries, because the lease is a database write and a page may
  hold the write lock; a mid-page 401 fails that item and refreshes at the next
  boundary.
- **Garbage collection.** When nothing refers to a connection any more (a sync
  deleted or switched account, a custom adapter deleted), it's deleted and its
  token revoked where the provider has a revoke endpoint.

**Pending sign-ins** survive a web restart: a short-lived
`sync_pending_sign_in:` row holds the public half, the keychain holds the secret
half (PKCE verifier, device code), and each is claimed once and purged when
expired.

### Sign-in methods

**Adapters never see secrets, and there is no script-defined sign-in.** A token,
key, or password enters a request only where the provider or connection block
declares it, filled in by the host or used by the host to sign the request. A
script that did its own sign-in would hold the secret, and a buggy or
prompt-injected adapter could send it to any host it's allowed to call; the host
allow-list can't catch that, because the host is legitimate.

| `auth_kind` | used by | the user does |
|---|---|---|
| `oauth2` | OneDrive, Google Photos, custom | signs in in the browser (PKCE, loopback return address), or enters a device code |
| `oauth1a` | custom (Flickr, SmugMug) | signs in in the browser; the host signs every request |
| `api_key` | Immich, custom | pastes a key, placed in a header (name, optional prefix), a query parameter, or a path placeholder |
| `basic` | custom (WebDAV, some NAS) | enters a username and an app password |

`sigv4` and `session` (log in with a password, keep a session token) are reserved
names: the connection-block check rejects them as not supported yet. They'll be
built when real services need them.

- **Every kind declares a `check` call**: a read-only request made right after
  sign-in that proves the credential works and supplies the account id and label.
  Graph's `/me` sometimes answers 504 right after a good sign-in, so the lookup
  retries server errors.
- **Device code** is offered as a fallback link (*Signing in on another
  device?*) for providers that support it, for Yaffo running on a home server
  opened from another machine.
- **Secret URLs** (share links that carry a token) are `api_key` with path
  placement. The UI asks for the link, and the host splits it into the host
  (checked against `base_urls`) and the secret part, which stays a connection
  placeholder (`{secret}`) filled in only as the request leaves, so it never
  appears in a check, a log, or a denial.

### Return addresses

The OAuth return address is built from the configured web port (`web.port`,
default 5001), never a random one, because a registered address must match
exactly. A connection block declares which case applies (`REDIRECT_KINDS` in
`providers.py`), and the UI shows the address only when the user has to register
it:

| `redirect` | the service… | the user sees |
|---|---|---|
| `registered` | only returns to addresses registered with the app | a copy-paste step, in the service's own words (`app_form_labels`) |
| `any_loopback` | accepts any loopback address (RFC 8252; Google's *desktop app* client, Microsoft public clients) | nothing |
| `per_request` | takes the address in each sign-in request (common with OAuth 1.0a) | nothing |
| `device_code` | supports device sign-in (RFC 8628) | the code and a link |

### Self-hosted servers

A provider with `instance: ["base_url"]` (Immich, custom self-hosted services)
asks for the server address at sign-in. The address becomes the connection's
allowed host at run time, appears in consent, and is part of the account id, so
two servers are two connections. Addresses are https by default. Plain http is
allowed only for a private-network host (private, loopback or link-local IPs, or
names under `.local`, `.lan`, `.home.arpa`, `.internal`) and only after the user
confirms a warning; it's never allowed for a public address
(`normalize_instance_url`).

### App credentials

Yaffo ships app registrations for its presets (`oauth_clients.py`; the Google
desktop secret is stamped at build time, see
[OAuth App Registrations](oauth-app-registrations.md)). A custom OAuth adapter has
no such registration, so the user registers their own developer app once, in the
builder's *Test account* tab. The client id and secret go into the keychain under
the adapter (`app_secret_ref`), are never stored in the database or sent to the
LLM provider, and are reused by every sync on that adapter. Changing them signs
out every connection for that provider, since tokens are issued to an app.

## Adapters

### Module layout

An adapter is **one Starlark module stored as one code blob**, with working and
published versions like an automation. It assigns a literal `ADAPTER` dict once,
at top level. `ADAPTER["hooks"]` maps hook names to **bare names of top-level
`def`s**; other top-level functions are private helpers the host never calls. The
presets in `yaffo/sync/presets/` are the reference examples.

`ADAPTER` declares `contract`, `role`, `provider` (a preset key, or `custom` with
a `connection` block), `title`, `scopes`, `options`, `hooks`, and for targets
`capabilities`. It's read from the AST, never by evaluation, so the UI, the
builder, and consent learn what an adapter does without running it. The extracted
metadata is stored as JSON with hook values replaced by their names.

Static rules (`adapter_static.py`), checked when the builder writes code and at
publish:

- Hook names must be known for the role. Required: `list_page` (source), `push`
  (target). Optional: `resolve_download` (source); `remove`, `update`,
  `list_remote` (target); `choices` (either).
- Options and hooks agree: a `remote_folder` or `remote_collection` option needs a
  `choices` hook, and a `choices` hook needs such an option.
- Capabilities and hooks agree: `remove: True` needs a `remove` hook and the
  reverse, and likewise for `update` and `reconcile` (`list_remote`).
- Top level may define constants and helpers only; top-level host calls are
  rejected.
- Every endpoint the detector finds must be under the connection's hosts.

### Hook invocation

To call a hook, the host evaluates the module with
`ADAPTER["hooks"][name](...)` as its trailing expression, arguments injected as
an input, through the same `run_starlark` path automations use. Each call is a
fresh `yaffo.starlark_worker` subprocess:

- **No module state survives between calls.** State persists only through
  `ctx["state"]` in and `state_patch` out, which makes every hook restartable at
  any boundary.
- **Limits apply per call** (the automation sandbox's time, host-call, and
  message limits). A 40,000-photo backfill is many small calls; the host loops,
  not the script.
- **Web time doesn't count against the time limit.** In the `adapter` profile the
  deadline pauses while `web_request` or `web_upload_media` runs; each has its own
  connect and idle-read timeouts, so a slow but progressing upload isn't killed.
  The host also chunks target batches by bytes (`max_batch_bytes`) as well as by
  count.
- **A process per call is cheap enough.** Hooks run per page or batch, never per
  item. An evaluator launch measured about 21 ms (2026-10-08), so a 40,000-photo
  backfill at 200 records a page costs about 4 s of launches, against hours of
  transfer time.

### The `adapter` host profile

Adapters run with their own small registry (`ADAPTER_HOST_API`), not the
session-bound `HOST_API`:

| function | notes |
|---|---|
| `web_request(connection, method, url, json=, headers=, query=)` | grant-checked, signed by the connection |
| `web_upload_media(connection, method, url, media_item_id, …)` | targets only; raw, multipart, or byte ranges for upload sessions; the host reads the bytes from the prepared batch item |
| `url_template(template, **values)` | typed placeholders, so the endpoint detector stays complete |
| `print(...)` | captured into the run's output |

No `data_query`, no Yaffo mutations, no progress reporting. An adapter's only
effect on the world is its granted web calls; its only effect on Yaffo is the data
it returns. `connection` is always the literal `CONNECTION`, bound to the sync's
connection, so one adapter works with any account.

### Contract versioning

The host supports exactly one contract, `CURRENT_CONTRACT` in
`adapter_static.py`. It's bumped for any change an existing adapter could get
wrong, including a change in the *meaning* of an unchanged field. There is no
compatibility layer: an adapter on another contract fails its check
(`contract_outdated`), is unavailable to run, and its syncs say so. The fix is
regeneration in the builder (*Regenerate*), which goes through the normal draft,
Test, and publish flow. Presets ship with the app and are always current.

Stored JSON (`automations.sync`, `sync_adapters` versions and grants) is Yaffo's
own data, not generated code, so it's **migrated**, not invalidated: a shape
change ships with a numbered migration that rewrites it. Anything that still fails
validation pauses that sync with `invalid_config` rather than being guessed at.

### Presets

`preset_seed.py` seeds `yaffo/sync/presets/*.star` into `sync_adapters`
idempotently whenever adapters are listed or loaded, so an upgrade re-seeds on
first use. A preset whose `preset_version` or code changed gets its new code,
metadata, manifest, and grants (presets ship with their grants, approved by the
review that shipped them). Because a Starlark adapter's part of the consent hash
covers its **grant set** rather than every byte of code, a preset fix that
doesn't widen what it may call leaves syncs on, and one that does asks again.

| preset | role | notes |
|---|---|---|
| OneDrive | source | delta feed: backfill then incremental; tombstones; folder layout from parent ids, cached in state |
| OneDrive | target | a folder per routing target, chunked upload sessions, deletes only with the copy's original eTag (`412` keeps a copy changed remotely); the Folder target's layouts |
| Google Photos | target | an app-created album per routing target, remembered in state; `list_remote` reconcile. Since Google's 2025 Library API changes an app can list and add to only albums it created, so the picker shows only those |
| Immich | source, target | the first `api_key` and self-hosted presets |

## Hook contract

### `ctx` and the return envelope

Every hook receives `ctx` with the sync's validated `options`, `state` (the
adapter's state plus a read-only `checkpoint`), and `run` (`preview`, `run_id`,
and `pass`: `backfill`, `incremental`, or `reconcile`).

Every hook returns a dict. **Unknown keys are an error**, not ignored, so a typo
can't silently drop data. Any hook may return `state_patch` (keys to set; `None`
deletes) and `error`. State is capped at 64 KiB per sync and commits atomically
with the results it describes.

**Errors are data**, dicts with a stable `code` the UI translates at render time:

| code | engine behavior |
|---|---|
| `rate_limited` | stop the run, keep the cursor, reschedule after `retry_after_s` |
| `retry_later` | transient (5xx, timeout): retry with backoff, then fail the item or page |
| `auth` | stop, mark the connection `needs_sign_in`, don't retry |
| `not_found` | targets: the remote item or album is gone (removal counts as done); sources: skip |
| `rejected` | permanent per-item failure; recorded, not retried |
| `fatal` | stop the run; the message goes to run history |
| `cursor_invalid` | sources: the cursor or checkpoint expired (e.g. OneDrive's 410 `resyncRequired`); restart from the checkpoint, or a full pass. Dedupe makes re-listing cheap |

A hook-level `error` may come with partial results. The host commits the results
and `state_patch` it got, then applies the error. Batch items with no result go
back to **unsent**, not `pending`: the adapter is saying it never got to them.
`pending` is reserved for items whose outcome is unknown because the run died
mid-call.

### Source hooks

- **`list_page(cursor, ctx)`** returns `records`, `next_cursor` (`None` ends the
  pass), and optionally `checkpoint`. `cursor` is `None` on a pass's first call;
  an incremental pass then starts from `ctx["state"]["checkpoint"]`. The host
  persists `next_cursor` after every page, so a backfill resumes mid-pass, and
  promotes `checkpoint` only after the final page, so a half-finished pass never
  advances the delta point.
- **`resolve_download(record, ctx)`** (optional) supplies the download URL right
  before downloading, for providers whose URLs expire.

**Records** (fields in `source_run._RECORD_FIELDS`; invalid records fail the
page): `source_id` and `filename` are required; `folder` (for the keep-folders
layout), `download`, `size`, `mime`, `hash`, `taken_at` (local, utc, offset),
`location`, `deleted` (a tombstone), and `updated_at` are optional. Download URLs
must be on the connection's hosts. Capture hints apply on a photo's first index
only, and only when the file has none of its own.

**Imports are files only.** Records carry no albums or captions. Few providers
have both, each means something different, and Yaffo has no description field.
Anything a camera or editor wrote into the file survives, because the file is
imported untouched.

### Target hooks

- **`push(batch, ctx)`** sends a batch and returns `results`, one per item: a
  `remote_id`, or an `error`.
- **`remove(items, ctx)`** (capability `remove`) removes items by `remote_id`;
  `media_item_id` is `None` for a photo deleted from Yaffo.
- **`update(items, ctx)`** (capability `update`) applies caption or routing
  changes to items already sent; `previous` holds the old values. A routing change
  is a move.
- **`list_remote(cursor, ctx)`** (capability `reconcile`) lists what the remote
  holds, for crash recovery and drift. It must report only items this integration
  can see.

**Batch items** carry `key`, `media_item_id`, `target` (the routing key),
`caption`, `filename`, `mime`, `size`, and `taken_at`. The adapter uploads with
`web_upload_media(CONNECTION, …, item["media_item_id"])` and never has the bytes
or the path. (Built-in handlers also get the file's `path`.)

**Results are matched on `key`**, the ledger row id as a string, which every
result must copy. Results may come back in any order. `media_item_id` can't serve
(it's `None` after a delete), `remote_id` can't (one remote item can be in several
targets), and `item_key` can't (it changes when a photo moves between targets).

**Capabilities:**

| capability | meaning |
|---|---|
| `max_batch`, `max_batch_bytes` | the host chunks to this many items and bytes (bytes default 200 MB) |
| `remove`, `update`, `reconcile` | which optional hooks exist; without `update`, moves become remove + push and caption changes are ignored |
| `targets` | `one` (ignores routing) or `many` (routes by target key) |
| `accepts` | optional `types`, `max_bytes`, `max_pixels`; omitted means the original file, whatever it is |

**Format conversion.** When a target declares `accepts`, the engine prepares
each batch item before `push`: an image of another type, or over the size or pixel
caps, becomes a **JPEG copy** (downscaled, quality stepped down to fit), keeping
the capture date, orientation, and location. The batch item describes the copy, so
the adapter never converts anything itself. Copies are made lock-free in a
temporary folder for the run and deleted afterwards; `sync_items.details` records
`sent_as`. **Videos aren't transcoded**: one the target doesn't accept fails with
`unsupported_format` and is listed on the run.

Every target is a **collection** whose items persist and can be listed and
removed. Send-only destinations (chat, webhooks, email) are out of scope. When a
selection change implies removals the target can't do, the run reports them
rather than dropping them silently.

### Options and the `choices` hook

Options are **per adapter**: each declares what a sync must be told, and the host
renders the *Options* step, validates the values, and passes them as
`ctx["options"]`. Each option has a `type`, `label`, and optionally `help`,
`default`, and `required`.

| type | rendered as | value |
|---|---|---|
| `string`, `int`, `bool` | the standard configure fields (`render_configure_form`) | the value |
| `select` | a dropdown of declared choices | the chosen value |
| `remote_folder` | a folder browser on the service | `{id, label}` |
| `remote_collection` | a list of the service's albums, sets, or buckets | `{id, label}` |
| `local_folder` | built-ins only: the in-app folder picker | an absolute path |

`choices(option, parent_id, cursor, ctx)` feeds both remote types. It's a live,
**read-only** call through the sync's connection (only `read`-risk grants), so the
*Options* step stays locked until the *Account* step is done. A remote value
stores the id, which survives renames, and the label (the picker's breadcrumb
path), which the form and consent show. Remote defaults can't be literals, so an
adapter marks a sensible item `suggested`. If a chosen folder or album no longer
exists at run time, the sync pauses with `option_missing`; it never falls back to
a default silently. Option values are part of the consent hash.

## Web access

`yaffo/sync/web.py` checks every call at the host boundary:

1. **Hosts.** The URL is https and on the connection's `base_urls`, which get the
   account's credentials, or `followup_hosts`, which never do. (Self-hosted http
   is the one exception; see *Self-hosted servers*.)
2. **Grants.** An endpoint grant whose method matches and whose pattern matches
   the URL's scheme, host, and path (query strings aren't part of a grant), or a
   **follow-up** grant for a URL Yaffo itself received from a granted call: in a
   response body or `Location` during this run, or stored by the engine as the
   sync's cursor or checkpoint after an earlier run vouched for it
   (`runtime.followups`). This is how OneDrive's `nextLink` and `deltaLink` pass
   without literals. Grants count only when approved for the adapter's current
   code hash.
3. **Read-only clients** (Test, the option pickers) allow only `read`-risk calls.

Denials and failures return to the script as data (`{"status": 0, "error": …}`),
never exceptions. Redirects are followed by hand, only to allowed hosts, and
credentials go only to `base_urls` (OneDrive personal downloads redirect to
`my.microsoftpersonalcontent.com`, which gets none). Responses expose a short
allowlist of headers.

## Selection

For a target, what leaves Yaffo and where it goes:

| kind | definition |
|---|---|
| `album` | a Yaffo album; membership mirrors to the target |
| `query` | the filter panel's selections in the sharing wire format (`media_filter_params.wire_filters`), re-evaluated each run |
| `script` | a selector script in the read-only `automation` profile (`data_query`, `face_similarity`, `match_people`; no mutations, no web) |

All three produce the **desired set**: `{media_item_id, target, caption}` per
photo. `target` is a routing key the adapter interprets (an album title, a
subfolder). The host validates the set and caps it at `DESIRED_SET_LIMIT` (5,000,
matching `data_query`).

The selector is a **separate script on purpose**. Adapters are reusable and
provider-specific; selections are user-specific. Keeping them apart means one
preset serves every user's rule, and changing the rule never touches the adapter's
grants. The selector lives in the sync's own `working_code` / `published_code`, so
it gets the existing builder chat, Test, and publish flow; its published code hash
is part of consent.

Sources import everything they list. The exception is the peer source's *What to
import*, a `query` selection the paired device applies to its own library
(`SyncConfig.source_filters`).

## Engine

### Source run

For each page from `list_page`, the engine validates the records, then for each
record, with bounded concurrency:

1. Skips it if the ledger already has its `source_id` with the same `updated_at`.
2. Links it without downloading if its hash matches a library photo's `sha1`
   (`linked_existing`).
3. Records a tombstone as `deleted_at_source` (see *Source deletions*).
4. Otherwise downloads to a temporary name in the import root, sniffs the type
   (images and videos only), enforces size caps and free space
   (`FREE_SPACE_MARGIN`), and atomically renames into place under the chosen
   layout.
5. Records watcher suppression so file sync doesn't index the file twice, and
   commits the record's media and ledger rows, releasing SQLite's writer lock.

The page's new files are queued for import and indexing, and the cursor and
`state_patch` commit after the page. Committing per record lets the web process
cancel a run between files; advancing the cursor per page means a crash replays
the page, and the committed ledger rows make its finished records idempotent.
A row's `details` gets `index_queued` once its index job is queued, so replay
also queues a file committed just before a crash.

- **Layout** (`import_root.layout`): keep the source's folders (default), by year
  and month taken, by year taken, or all in one folder. Undated files go under
  `Undated`.
- **Failed downloads are retried** at the start of later runs, up to
  `MAX_ITEM_ATTEMPTS` (5), from the record kept on the ledger row, since a delta
  feed won't list them again.
- **Hash backfill.** A source run hashes un-hashed library photos first, within a
  third of its time budget, so dedupe can match them.

### Edited at the source

A record that comes back with a new `updated_at` and different content (hash, or
size when there's no hash) **replaces the library's copy**: the new file
atomically replaces the file at the media item's current path, the old version
goes to the OS trash, and the media item keeps its id, so tags, labels, favorites
and albums stay. The item is re-indexed (which re-detects faces, so assignments on
that photo are made again) and `media_modified` fires. Same content with only a
new `updated_at` just updates the ledger.

### Source deletions

**Yaffo never deletes a local file because the source deleted it.** A tombstone
only sets the ledger row to `deleted_at_source`. The library is the master copy;
a remote deletion is often cleanup of cloud storage, and deletions can be wrong
(a reset delta token, a provider bug). The Items panel lists these photos so the
user can delete them in Yaffo if they want, and the run summary counts them. A
tombstone for an item never imported is recorded so it's skipped if listed again;
an item that reappears goes back to `imported`. Folder and peer sources never send
tombstones: files disappearing there is normal.

### Target run

The engine computes the desired set, compares it with the ledger, and pairs up
**moves** (a photo leaving one target and entering another in the same run).
Moves and caption changes go to `update` when the target supports it (otherwise
moves become remove + push and captions are ignored). New items are pushed, and
items no longer desired, plus rows marked `removing`, are removed. For each chunk,
by count and bytes, it writes intent rows (`pending`), calls the hook, and commits
the results and `state_patch` in one transaction.

**Ledger keys.** With `targets: "one"` the key is the photo id, so a routing
change is a no-op. With `targets: "many"` it's `media_item_id:target`, and
renaming the Yaffo album that's the routing key is an `update` per photo, never a
re-upload.

**Crash recovery.** If a run dies after `push` sent data but before its results
committed, the next run finds `pending` rows. With `reconcile`, the engine calls
`list_remote` and matches them to remote items, so nothing is re-uploaded. Cloud
presets make that match exact: uploaded filenames carry a stable
`__yaffo_<ledger id>` suffix before the extension, and the engine restores the
remote id and adapter state from the match. Without `reconcile`, the engine
re-pushes and notes possible duplicates.

**Drift.** A reconcile pass every `RECONCILE_EVERY` (7 days) marks items the user
deleted remotely `removed_remotely`; they aren't re-sent unless the user asks.

### Photos deleted from Yaffo

Deleting a photo removes it from every target it was sent to, but not inside the
delete: a delete must be instant and always succeed, bulk deletes would otherwise
make hundreds of one-by-one calls, and removals must respect the one-run-per-sync
lock. So the work is queued in the delete and done by the next sync run
(`yaffo/sync/deletes.py`):

1. **In the delete's own transaction**, every sent or `pending` target row for the
   photo becomes `removing`, keeping `remote_id` and `target`, and its
   `media_item_id` is cleared explicitly (SQLite foreign keys are off). Both
   row-deleting paths do this: `media_repository.delete_media_items` and
   `index_photos.delete_orphaned_media_items` (used by the watcher, file sync, and
   the duplicate cleaner).
2. **After commit**, `media_deleted` fires with the photo ids and the affected
   syncs. Each target sync owns a trigger for it, filtered to that sync and passed
   through the settle window, so deleting 300 photos is one run per sync.
3. **The sync run** removes `removing` rows in batches. Removal is idempotent
   (`not_found` counts as removed); a failure leaves the row for the next run. A
   removed row with no photo is deleted from the ledger.

A target that can't remove marks the rows `cant_remove` and says so.

### Validation and run control

- **Adapter output is untrusted.** Unknown keys, wrong types, or missing fields
  fail the page or call with `fatal`, so a buggy adapter is never half-applied.
  Download and follow-up URLs are limited to connection hosts, results must name
  only keys from their batch, each once, and `state_patch` is capped.
- **One run per sync**, via a queue lock named per automation
  (`Task.enqueue_locked`). The scheduler tick leaves a due deferred trigger alone
  while that sync has a running Job.
- **Time budget.** A run stops at `DEFAULT_TIME_BUDGET_SECONDS` (15 minutes) at a
  safe boundary and continues later; long backfills are just several runs.
- **Temporary files** are named per sync (`.yaffo-<sync id>-….part`), and each run
  sweeps its own sync's leftovers first (peer partials younger than a few days are
  kept for resume). A retried copy reuses an identical file already at the
  destination name instead of making `IMG (2).jpg`.

## Triggers and events

Syncs use ordinary automation triggers: a schedule, or events. New syncs get
defaults: nightly for sources; for album targets, *when the album changes* plus
nightly; for peer sources, *when the device comes online* plus nightly. Three
domain events in the general catalog (`EVENTS` in `models.py`) were added for
syncs, and any automation can subscribe to them:

| event | emitted | payload |
|---|---|---|
| `album_changed` | after commit, by the album routes and the host album actions (so automations and assistant plans emit it too, with their causal chain) | `album_id`, `change` (`added`, `removed`, `renamed`, `deleted`), `media_item_ids`; bulk operations set `truncated: true` instead of listing ids |
| `media_deleted` | after a delete that marked ledger rows (see above) | photo ids, `sync_automation_ids` |
| `device_online` | by the web process when a paired device goes from absent to present, on the hub (presence polled) or the LAN (mDNS) | `device_id`, `path` (`hub` or `lan`) |

Reorders and cover changes don't emit `album_changed`: they don't change what an
album contains. `album_id` and `change` reach Starlark as `ctx["album_id"]` and
`ctx["change"]`.

**Per-trigger filters** (`event_filters.py`) live in the trigger's `config`:
`album_id` and `changes` for `album_changed`, `sync_automation_id` for
`media_deleted`, `device_id` for `device_online`. Matching happens in Python
after the dispatcher's query.

**Settle window.** A trigger with `config.settle_seconds` (syncs use
`SETTLE_SECONDS`, 60) doesn't start a run. Dispatch sets the trigger's
`next_run_at` to now plus the window, only if it's currently empty (one
conditional `UPDATE`), and the schedule tick starts the run when it's due. A
change during a run sets it again, so the next tick after the run picks it up. A
burst of album edits, a bulk delete, or a flapping device is one run, and since
the run diffs the whole selection, it doesn't matter what ids the events carried.
An album deletion skips the window and pauses the sync with `selection_missing`
at once.

**Loop guard.** Syncs never write Yaffo albums, so a sync can't re-trigger itself
through `album_changed`. A custom script can still chain them (import, then add to
an album that a target sends); the causal chain records both, and the existing
guard breaks a loop. A deferred run starts with an empty chain, which is safe
because targets never write albums.

## Built-in sources and targets

Built-ins are `sync_adapters` rows with `is_preset` set and a `handler`
(`folder_source`, `folder_target`, `peer_source`) instead of code. They implement
`SourceHandler` / `TargetHandler` in Python with the same envelopes as the hook
contract, and share everything else: ledger, selection, triggers, conversion,
Test, consent, and setup. A built-in target answers `reconcile` with present or
missing per ledger row instead of `list_remote`.

### Folder source

*Import from… › Folder* **copies** new photos from a folder into the import root:
an SD card, a desktop sync app's camera-upload folder (which gives Dropbox import
with no API), Downloads, or a shared folder. It differs from adding the folder as a
media folder, which indexes files in place; the setup points that out.

- **Records:** one per file. `source_id` is the path relative to the folder, size
  plus modification time act as `updated_at`, and the content sha1 is the hash, so
  files already in the library are linked and edited files replace the library's
  copy.
- **Originals are never touched**, and there's no delete-after-import. No
  tombstones.
- **A missing folder** (card not inserted) ends the run with "waiting for the
  folder", an ordinary outcome.
- **Guard:** the folder can't be inside a media folder or any sync's import root.

### Folder target

*Send to… › Folder* keeps a copy of the selected photos in a folder outside the
library, for whatever watches it: the Amazon Photos desktop app's folder backup,
Dropbox, Google Drive, OneDrive or Synology Drive desktop apps, a NAS share, or a
USB stick or SD card for a photo frame.

- **Options:** the folder; layout (flat, a subfolder per routing target, or by
  capture year and month); *Convert to JPEG* (off, on, or sized for a photo frame,
  which set `accepts`); and *When a photo leaves the selection* (remove from
  destination, or keep). With a custom selector rule, the layout is fixed to a
  subfolder per target, so the rule's routing isn't discarded.
- **Writing:** copy to a temporary name, then rename, so a watcher never sees a
  half-written file. The modification time is set to the capture date. Collisions
  get a suffix. The copy's relative path is its `remote_id`; size and modification
  time go in `details`.
- **Removal is limited to Yaffo's own unchanged copies**: a file is deleted (to the
  OS trash) only if the ledger says Yaffo wrote it and its size and modification
  time still match; anything else becomes `cant_remove`. With *keep*, removals
  succeed without deleting (`kept`). Yaffo never lists, renames, or deletes
  anything else in the folder.
- **Reconcile** checks only the ledger's paths.
- **Guards:** the folder can't be inside a media folder or the thumbnail folder.
  A missing folder pauses the sync with `destination_missing`, and free space is
  checked per batch.
- Yaffo knows only that a copy was **written**, not whether a desktop app uploaded
  it, so the UI says "written to folder", never "sent".

## Peer sync

A **peer sync** is a source whose other side is a paired Yaffo device: "Copy new
photos from *Mom's laptop › Vacation 2026* into *Pictures › Imports › Mom*". It
builds on [P2P Device Sharing](p2p-sharing.md). Sending to a device, and library
replication generally, are out of scope.

| sync concept | for a peer |
|---|---|
| Connection | the trusted `known_devices` row; no OAuth, no token |
| Adapter | the built-in `peer_source` handler |
| Option: what to import | one scope the peer has granted: a media dir, folder, or album |
| Listing | pages of `list_files` manifests |
| Download | a chunked pull with resume and checksum |
| Authorization | the **serving** device's grant, re-decided per request by `granted_media_query` |
| Consent | Enable on the **importing** device |

### Process model

Sync runs execute in taskq workers, but the P2P engine has to stay a single
instance in the web process. A second process running the engine with the same
device identity breaks presence and calls:

- the hub keeps one socket per device and closes the older one (`CLOSE_REPLACED`)
  when another authenticates, and both sides reconnect after 2 s, so two processes
  keep replacing each other;
- hub messages reach whichever socket holds the slot, so replies to one process's
  calls land on the other, and the device appears to go offline at random;
- the hub's connect limit (30 per minute per IP) is shared, and exceeding it locks
  out the web process too;
- the UDP port and mDNS name are single-owner, and taskq runs several workers that
  recycle.

So **workers never run the engine**. They call an internal REST API on the web
process, which makes every peer call through its one engine. A local socket was
rejected as a second channel to build and test; inherited pipes don't work because
web and the taskq host are siblings in dev; a LAN-only worker engine would work
only on one network; and multi-socket devices would need a hub protocol change.

### Internal P2P API

`/internal/p2p/v1/…` (`yaffo/routes/internal_p2p.py`), JSON in and out, kept out
of `/api/`, navigation, and the assistant's pages. It moves no file bytes to the
caller; the web process writes downloads itself.

**Request checks.** The web UI has no login, so the API has to tell *a Yaffo
process on this machine* apart from *a web page in the user's browser* and from
other machines. All three checks are required; otherwise `403`:

- **Loopback caller** (`127.0.0.1` or `::1`), even when the server binds more
  widely, which keeps out other machines.
- **Loopback `Host` header**, which defeats DNS rebinding: a rebinding page is
  same-origin as far as the browser is concerned, but still sends its own `Host`.
- **`X-Yaffo-Internal: 1`**: a cross-site page can't add a custom header without a
  CORS preflight, which the routes never answer, so cross-site form posts fail.

A secret token was considered and dropped: its only extra protection is against
other local programs or OS users, who can already use the anonymous UI routes. The
app-wide `Host` and origin checks are covered separately in
[Web Routes and Request Security](web-routes-and-request-security.md).

**Discovery:** the web heartbeat publishes `extra.internal_api = {port, version}`
in `web_status.json`, which the client (`yaffo/sync/peer_client.py`) reads.
**No long requests:** every call returns within about 10 s, because waitress's
small thread pool is shared with the UI; long work is started, then long-polled.
Demo mode holds pulls to the remote gallery's caps.

| resource | purpose |
|---|---|
| `GET status` | engine availability, device id, hub connection |
| `GET devices` | paired devices with trust state, `online` (hub; `null` when the hub is unreachable) and `local` (LAN) |
| `GET devices/{id}/scopes` | the scopes the peer grants this device, each with a deterministic `scope_key` (`album:<id>`, `media_dir:<guid>`, `folder:<guid>:<path>`) and the peer's protocol version |
| `GET devices/{id}/files` | one `list_files` page: the stable `source_id` (`media_dir_id:relative_path`), a per-listing pull handle, size, mtime, date, and sha1; optional `session` and `filters` (wire filters the serving device applies after the grant) |
| `POST devices/{id}/sessions`, `GET` / `DELETE sessions/{id}` | one pinned connection to a peer (LAN first, else relay with one direct-path upgrade attempt), with a relay policy, idle expiry after 120 s, and sharing between syncs by reference count |
| `POST sessions/{id}/pulls`, `GET` / `DELETE pulls/{id}` | download one file to a caller-chosen destination; long-poll the state with `wait` (at most 10 s); cancel |

**Destination validation** is the write's security boundary: the path must be
absolute, resolve inside a configured media dir, have a sync temp-file basename,
have an existing parent, and not be a symlink; anything else is
`bad_destination`. Pulls write `<destination>.partial` plus a sidecar and rename
once the whole-file sha256 matches, using the same resume rules as remote-gallery
transfers. Pairing, grants, revocation, and previews deliberately stay UI routes,
so nothing that gets past the checks can change trust or sharing.

**Errors** are `{"error": {"code", "message"}}` with matching HTTP status. The
codes and what the peer source does with them:

| code | meaning | peer source does |
|---|---|---|
| `engine_unavailable` | P2P is off or failed to start | run ends "sharing is off" |
| `peer_offline` | not on the LAN and unreachable through the hub (also: the web process refused the connection) | run ends "waiting for the device" |
| `relay_not_allowed` | only a relayed path exists and the session forbids relay | run ends "waiting for a direct connection" |
| `relay_budget_reached` | the session used its relay budget | stop at a page boundary; retry later |
| `not_trusted` | unknown or revoked device | pause, `device_revoked` |
| `scope_not_shared` | the peer no longer grants this scope | pause, `option_missing` |
| `file_refused` | the peer refused one file | that item: skipped |
| `source_changed`, `checksum_mismatch` | the file kept changing, or the checksum didn't match after a restart | that item: retry later |
| `bad_destination` | the destination failed validation | `fatal` (an engine bug) |
| `session_expired` | unknown or idle-expired session | open a new session and continue |

**`PeerPuller` and `TransferManager`.** `PeerPuller` (`yaffo/p2p/puller.py`)
holds a session and pulls one file: chunking, resume, checksums,
restart-on-change, and relay accounting and throttling. `TransferManager` keeps
what the remote gallery needs (batches, manifests, layout, the interactive
"continue anyway" relay budget) and uses `PeerPuller` per file. The internal API
is its second user. Sessions and pulls live in memory on the engine loop; a web
restart loses them, and the next run resumes from the partials on disk.

### Peer source

- **Setup:** a *Device* step in place of *Account* (trusted paired devices only;
  pairing happens on the Sharing page), a *Shared folder or album* picker fed by
  `scopes`, and *What to import*: everything, or only photos matching a filter.
  The filter screen (`sync_peer_filter`) is the filter panel over the **device's**
  library, its option lists taken from the peer's facets within the grant
  (`remote_filters_context`, shared with the remote gallery). Its people and label
  ids are the peer's own, so choosing another device or share clears it.
- **The device is an option** (`{id, label}`), so validation and consent cover it.
  Pages show the device's current name (`shown_options`), so renaming a device
  doesn't change the consent hash.
- **Records** map from the manifest: `source_id`, `filename`, `folder` relative to
  the scope, `size`, `updated_at` as `size:mtime` (so a changed file follows
  *Edited at the source*), `hash` from the peer's sha1, and `taken_at`. No
  tombstones: Yaffo can't tell "deleted" from "no longer shared".
- **Run:** a revoked or unknown device pauses with `device_revoked`; an offline one
  ends the run normally. A session opens lazily on the first fetch and closes at
  the end. A run that hits its time budget cancels the in-flight pull and keeps
  the partial.
- **Throughput:** the handler runs ahead by up to three files (`plan_fetches`),
  starting pulls for the next records to their deterministic temp names
  (`.yaffo-<sync id>-<sha1(source_id)>.part`) so the connection isn't idle between
  files; a pull started ahead but never reached resumes next run.
- **Indexing:** each committed page queues its own import and index Jobs, which run
  while downloading continues; Run history shows only the sync run.

**Relay policy.** Relay egress is the hub's only metered cost, and unattended
backfills are what the policy discourages. Peer syncs always open sessions with
`relay: never`: they run on the LAN or a successful direct upgrade, and otherwise
end with "waiting for a direct connection". Remote-gallery batches, where someone
is watching, keep the interactive budget.

**Wire protocol 2.** Messages carry `"protocol": 2` (missing means 1), and
manifests include the stored `media_items.sha1` (`null` if not computed yet;
browsing never hashes contents). Each side adapts to the lower version: a v1 peer
sends no hashes, and the importer dedupes after downloading. With hashes, two
devices importing from each other move no duplicate bytes (`linked_existing`), and
the Sharing page warns when a grant back to a device contains its peer sync's
import root. Serving-side rate limits per peer (`yaffo/p2p/rate_limits.py`) are
checked after the signature.

## Test / preview

Test (`yaffo/sync/preview.py`) shows what a run would do now, without changing
anything:

| | source | target |
|---|---|---|
| web reads | live, page after page until the listing ends or `SOURCE_LIST_BUDGET_SECONDS` (5 s) pass; `read`-risk grants only | live `list_remote` where granted |
| web writes | none | recorded, not sent |
| library | no writes, no downloads | no writes |
| shows | counts of new, edited, already in the library, already imported, and deleted at source, plus a per-record table | counts to copy, to remove, already there, converted, and can't be sent, plus the planned action and destination per photo |

The budget exists because Test runs on the web server's small thread pool. When it
stops early it says how many records it counted, out of the source's total when
the source reports one. The table is paginated with the shared controls; changing
pages reruns the read-only Test.

A sync's Test uses that sync's connection. The builder's Test (`test_adapter`)
uses the custom adapter's test connection (`test_connection_key`); with none
signed in, its reads return recorded placeholders like its writes.

## Authorization and consent

Two layers, matching two separate questions:

1. **Adapter grants: what the code may call.** Endpoints are detected statically
   (literal connection, method, URL template), approved at publish, and re-opened
   when the code widens. Presets ship with their grants. Each grant carries a
   risk (`read`, `write`, or `shares_externally`); endpoints that share
   externally, like creating a public link, keep a separate confirmation.
2. **Sync approval: what the engine will do with it.** Enabling a sync stores its
   **consent hash**. For a web or peer sync, *Enable* first opens a review: where
   from, which account or device, which photos, into where and how arranged, what
   happens to removals and deletions, and the hosts Yaffo talks to. For the Folder
   built-ins, the setup panels themselves are the review.

The consent hash (`consent.py`) covers the role, the adapter (for Starlark, its
provider, scopes, and grant set; for a built-in, its handler), the connection and
needed scopes, the options, the import root and layout, and the selection (kind
plus album id, query, or the selector's published code hash). The engine
recomputes it at run start; a mismatch sets `needs_consent` and the run stops
before any web call or file write. Consequences:

- **Every new version of the selection re-asks**: a different kind, query, album,
  or newly published selector. Changes to the *members* of a selected album don't
  (the album is the user's consent, photo by photo).
- **Changing the account, options, import root, or selection turns the sync off**
  until it's enabled again.
- **Deleting a photo from Yaffo removes it from targets** that support removal, and
  the review says so.

## Building a custom adapter

*Add a new service…* opens the builder chat on a new custom adapter. The model
writes **only the adapter**: the hooks, plus the provider's `connection` block.
The engine, ledger, retries, and file writes stay in the host. Once published, the
adapter appears in *Import from…* / *Send to…* tagged "custom".

### The `connection` block

`ADAPTER["connection"]` is a literal, checked by `provider_from_block` in
`providers.py` on write and at publish:

- **Allowed keys** are fixed (`_BLOCK_KEYS`): `auth_kind`, `label`, `base_urls`,
  `followup_hosts`, `instance`, `check`, the `api_key` placement and labels,
  `auth` (OAuth endpoints and scopes), `redirect`, `app_credentials`, and
  `app_form_labels`. Unknown keys are an error.
- **`auth_kind`** must be one of the custom kinds (`oauth2`, `oauth1a`, `api_key`,
  `basic`); OAuth 1.0a allows only the `registered` and `per_request` redirects.
- **Every URL is https**, and `base_urls` and `followup_hosts` are plain origins.
  A self-hosted service (`instance: ["base_url"]`) has no fixed `base_urls`.
- **The block is part of the code hash**, so changing a host, token URL, or scope is
  a code change: draft, Test, publish, and re-consent for every sync using it.

A custom adapter's connections use `provider: "custom:<slug>"`; their endpoints,
hosts, and scopes are read from the adapter's published metadata (the working
version while building), not copied into the connection row.

### Where API knowledge comes from

1. **What the model already knows**, good enough for a first draft against a big,
   stable API but never trusted alone.
2. **Documentation**, through `fetch_docs`.
3. **Live responses in Test**, the decisive source: the model sees the real JSON
   and fixes its mapping. Docs get it close; real responses get it right.

### Builder tools

| tool | does |
|---|---|
| `write_adapter_code` | saves the draft; returns static-check errors (metadata, hooks, connection block, endpoints) |
| `test_adapter` | runs Test; returns records or the diff plus recorded calls, trimmed for the model |
| `fetch_docs(urls)` | fetches a batch of pages or OpenAPI specs **from Yaffo** and returns readable, size-capped text per URL |
| `read_sync_failures` | reads back the real runs of syncs using the service (`yaffo/sync/failures.py`): each sync's last run, its items by status, and its most recent failed items with the code, the service's message, attempts, file type, size, and the capture date as Yaffo passed it. Test never sends writes, so a target's refusals only show up here. Items are described without filenames or paths, so it needs no permission |

These are ordinary builder tools that Yaffo executes, so they work with every LLM
provider and every request goes through Yaffo's authorization. LLM providers'
built-in search tools aren't used: they run inside the model call, where Yaffo
can't show or approve them. None of these tools exist in the `adapter` profile.

### Permissions

**Web access is authorized by domain, deterministically.** A request runs if and
only if its registrable domain (eTLD+1, from the bundled Public Suffix List
snapshot, never fetched at run time) is in the adapter's authorized set,
`sync_adapters.doc_domains`. Nobody judges a URL's path or content. The set starts
empty and grows only when the user approves a request the model actually makes; a
URL that merely appears in the chat authorizes nothing. Redirects are followed only
within authorized domains.

| action | risk | approval |
|---|---|---|
| Read documentation (`fetch_docs`) | an outbound request; untrusted content | one prompt per batch, listing only the new domains with their URLs. Authorized domains fetch without asking; denied URLs come back to the model as denied |
| Live read calls in Test | real account data reaches the model, and so the LLM provider | the user clicking *Test* is approval. For the builder to test live on its own, the user approves once per adapter (`live_reads_allowed`); without it, model-run tests use placeholders. Only `read`-risk grants |

Writes are never sent from Test. The host caps what live calls return to the model
(the first few records, long fields trimmed, most headers dropped). Prompts use the
global confirm dialog or an inline chat card.

### Fetched docs are untrusted

A docs page could tell the model to call another host or send data somewhere.
Nothing the model writes takes effect without passing the existing checks:
endpoints are detected statically and must be under the connection block's hosts;
those hosts are listed in the enable review, and changing them re-asks consent;
`shares_externally` endpoints keep their own confirmation; and an adapter can't
read files or Yaffo data beyond what the engine hands each hook. A poisoned page
can make the model write a bad adapter, but the user sees any unexpected host
before it runs.

### What can't be built

Services with no public API or one closed to new apps (iCloud Photos, Instagram's
personal API); sign-in outside the supported kinds (client certificates, signed-JWT
service accounts, OAuth client credentials, scraped web logins); and sources that
need a person in the loop or local OS access (the Google Photos Picker, Takeout
archives, the macOS Photos library), which would be host-native producers rather
than adapters.

## UI

A sync is a **type of automation**. It lives in Library › Automations, using the
existing detail page, trigger panel, and run history. There is no Connections page
and no Adapters page.

- **New automation** asks for a type first: *Custom* (the script builder), *Import
  from…*, *Send to…* (presets first, then custom services), or *Add a new
  service…*.
- **Setup is one page** of collapsible panels: *Account* (or *Device*), *Folder and
  options*, *What to send* / *Where to put them*, and *When*. Completed panels fold
  to summaries, an unfinished sync can't be enabled, and the *Account* step reuses
  an existing connection, offers to extend its scopes, and shows when it's shared
  with other syncs.
- **Header actions:** Enable / Disable (a switch), Test, and Run now. An attention
  message under the header explains anything that needs action (sign in again, a
  missing folder, a service that needs updating).
- **Below Run history**, an *Items* panel shows the ledger: a search box (file
  names), a chip per status that occurs, the table, *Retry failed*, and shared
  pagination. Paging, search, and counts are queries; only one page is rendered.
- **Custom services** are reached from any sync that uses them (*Edit service*),
  which opens the builder with *Overview*, *Test account*, *Web access*, *Code*, and
  *Test* tabs and the publish modal. Presets never show code.

Early static mockups: [sync automation pages](assets/automation-sync-adapters/sync-automation-mockup.html),
[Add a new service](assets/automation-sync-adapters/add-service-mockup.html), and
[account types](assets/automation-sync-adapters/account-types-mockup.html). The
built pages differ in styling and detail.

## Data model

**Configuration and per-sync state are JSON; the per-item ledger is a table.**
Configs, grants, adapter state, and cursors are read and written whole, one sync
at a time, by the engine, and validated in code at every write and load (frozen
dataclasses in `schemas.py`, each with a `jsonschema` validator). Ledger rows are
different: tens of thousands per sync, looked up by key and status on every page,
joined to `media_items`, and they must follow deletes.

```
 application_settings ◄── automations.sync.config.connection_key   ("sync_connection:…" rows)
                      ◄── sync_adapters.test_connection_key
 sync_adapters ◄───────── automations.sync.config.adapter_id
 automations
   .sync                              config (UI-written) + runtime (engine-written)
   .working_code / .published_code    the selector script
     └──< sync_items ──► media_items
 conversations.sync_adapter_id ──► sync_adapters   (builder chat for a custom adapter)
 media_items.sha1                                  (dedupe)
```

### Tables and columns

| object | notes |
|---|---|
| `automations.sync` (JSON) | two parts with different single writers: `config` (`SyncConfig`: role, adapter, connection key, needed scopes, options, import root or selection, source filters, consent hash) written by the UI and setup; `runtime` (`SyncRuntime`: status, pause code, adapter `state`, `cursors`, `followups`) written only by the engine, in the same transaction as the ledger rows it describes |
| `sync_adapters` | one row per adapter: slug, name, role, provider, `is_preset`, `preset_version`, `handler` (built-ins), working and published code with their `AdapterVersion` JSON (code hash, contract, metadata, endpoint manifest), `grants`, and for custom adapters `doc_domains`, `app_secret_ref`, `test_connection_key`, and builder state. `status` is draft, ready, or invalid |
| `sync_items` | the ledger, below |
| `application_settings` | no schema change; `sync_connection:` and `sync_pending_sign_in:` rows of type `json` |
| `media_items.sha1` | indexed; computed at index time, backfilled for older rows |
| `conversations.sync_adapter_id` | the builder chat, like `automation_id` for automations |

Runtime status is one of `ready`, `needs_consent`, `needs_sign_in`, `paused`
(with `pause_code`: `selection_missing`, `destination_missing`, `option_missing`,
`device_revoked`), or `invalid_config`. The adapter sees `runtime.state` merged
with the checkpoint as `ctx["state"]`; cursors aren't in it, so a `state_patch`
can't overwrite them.

### `sync_items`

One table for both directions, unique on `(automation_id, item_key)`, which makes
re-listing and re-pushing idempotent. For targets each row doubles as the intent
log: written `pending` before `push`, resolved after.

| column | source | target |
|---|---|---|
| `item_key` | the provider's `source_id` | `media_item_id`, or `media_item_id:target` for `targets: "many"` |
| `media_item_id` | the imported photo | the sent photo; cleared on delete |
| `remote_id`, `target` | — | the provider's id (or the Folder target's relative path) and routing key |
| `fingerprint` | the record's `updated_at` | a hash of the caption, for `update` |
| `status` | `imported`, `linked_existing`, `skipped`, `failed`, `deleted_at_source` | `pending`, `sent`, `failed`, `removing`, `removed`, `removed_remotely`, `cant_remove` |
| `details` (JSON, never queried) | relative path, sha1, the record for retries, `error_message` | album id, `sent_as`, the Folder copy's size and mtime, `error_message` |

Plus `error_code`, `attempts`, `run_id`, and `updated_at`, with indexes on
`(automation_id, status)` and `media_item_id`. Statuses and error codes are stored
as codes and translated at render time, like Job outcomes. When an item fails, is
skipped, or can't be removed, `record_item_error` (`run_common.py`) also keeps the
handler's own message in `details.error_message` (English, capped at 500
characters) and logs a warning naming the sync, item, code, and message. A retry
clears it, and a success replaces `details`.

### What JSON gives up, and how code covers it

| lost | covered by |
|---|---|
| `RESTRICT` on deleting a custom adapter in use | the delete route checks for syncs first and lists them |
| an FK from a sync to its connection | the engine loads the connection at run start; a missing row means `needs_sign_in` |
| `SET NULL` on deleting the selected album | the engine checks the album exists; if not, it pauses with `selection_missing` rather than treating "no album" as "remove everything" |
| column types and constraints | `jsonschema` on every write and load |
| partial updates | each part has one writer and is replaced whole; one run per sync at a time |

### Lifecycle rules

- **Delete a sync:** its ledger rows cascade. Its connection goes, with its token
  revoked, only if nothing else refers to it. Nothing is removed remotely and no
  imported file is deleted.
- **Sign in again:** replaces the connection's keychain entry in place for every
  sync using it. Signing in as a *different* account picks or creates another
  connection, which changes the consent hash.
- **Delete a custom adapter:** refused while syncs use it; otherwise its app
  credentials leave the keychain and its test connection is released. Presets
  can't be deleted.
- **Republish a custom adapter:** a new code hash. Grants for the old hash stop
  authorizing calls. A manifest that only narrows re-stamps grants automatically;
  otherwise the publish modal asks, and syncs re-ask consent only if the grant set
  changed.
- **Preset upgrade:** the same rule; a widened manifest pauses affected syncs with
  `needs_consent` instead of granting silently.

### Migrations

| migration | adds |
|---|---|
| `016` | the sync tables and columns |
| `017` | the ledger index used for sorting by update time |
| `018` | the import layout; existing syncs keep year-and-month and their consent is re-stamped |
| `019` | the built-in `peer_source` row |
| `020` | the adapter builder's columns |
| `021` | builder live-read approval |

## Design rationale

**Why split the pipeline.** Broken into steps, almost all of import and export is
provider-independent: diffing, state, batching, retries, resume, crash recovery,
downloads, file writes, metadata merge, and indexing. Only listing and mapping
records (import) and creating albums, uploading, and removing (export) are
provider-specific. An earlier proposal had each user's automation script make
its own web calls and run the whole pipeline; that regenerates the error-prone
parts every time: pagination, delta tokens, partial failures, and the crash window
between "uploaded" and "recorded". Writing it per provider in Python means
maintaining N pipelines. Adapters keep the provider-specific code to roughly
50–150 lines of Starlark, and the host gets everything else right once.

**Why hooks are declared in `ADAPTER["hooks"]`.** Finding hooks by naming
convention makes a misspelled `def pussh` silently "not implemented". One script
per hook duplicates helpers and multiplies hashes and manifests for one consent.
Explicit registration in one module makes the hook set a static fact: typos,
missing required hooks, and unknown names are errors. The cost is that `ADAPTER`
holds function references, so it's read from the AST.

**Why presets are Starlark.** They're the first users of the hook contract, so the
contract was proven against real providers before the builder generated adapters
against it, and presets and custom adapters share one code path.

**Why local before cloud, and read before write.** The Folder built-ins exercise
nearly the whole engine (ledger, diffing, crash recovery, consent, triggers,
conversion, setup) with no external dependencies. The first cloud preset was a
source, which can't damage anything remotely.

## Limits, deferred work, and open questions

**Not supported:**

- **Two-way sync.** An *Import from* and a *Send to* on the same place aren't a
  mirror, by design: the library is the master copy, source deletions never delete
  local photos, edits made in Yaffo aren't sent back, and imports carry no albums.
- **Imports carry no albums or captions**, and sending to a paired device isn't
  supported.
- **Targets with no usable API:** Amazon Photos (use the Folder target with the
  Amazon Photos desktop app's folder backup), Frameo and most photo frames (the
  Folder target on a card or stick, or a frame that shows a Google Photos album).
- **Email targets** are deferred: email isn't an HTTP API, sent mail can't be
  removed, and the audience (email-enabled frames) is small.

**Deferred:**

- The `sigv4` and `session` sign-in methods, until real services need them.
- `web_search` for the builder, which needs a configurable search backend.
- A `volume_mounted` trigger for Folder sources (import when a card is inserted).
- A `modified_since` filter on peer `list_files`, and binary chunk framing.
- Library-page integration: an album chip for syncs that send it, "imported from"
  and "sent to" on photo details, and *Imported by* / *Sync failed* filters.

**Open questions (peer sync):**

- Should importing a peer's album also add the photos to a local album of the same
  name, by default or as an option?
- When access is revoked mid-run, should the run pause at once or finish the page
  first?
- Should several syncs from one device run one at a time, rather than only sharing
  a session?

**Verification still outstanding** (not code gaps): a live worker kill and restart
mid-batch against a cloud target, Google's OAuth verification (until it passes,
only listed test users can sign in to Google Photos, with 7-day tokens), the
two-machine LAN run for peer sync with timeouts tuned against real NATs, a builder
run producing a working Flickr target from a one-line request, and two-way Immich
sync against a local Immich in the UI test sandbox.
