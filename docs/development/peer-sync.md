# Peer Sync — Importing from Paired Devices (Design Sketch)

**Status: proposed.** Builds on [Sync Adapters](automation-sync-adapters.md) (the
sync engine, the item ledger, sources and targets) and
[P2P Device Sharing](p2p-sharing.md) (pairing, share grants, the wire protocol,
`TransferManager`). Read those first; this doc only covers what's new.

A **peer sync** is a sync **source** whose other side is a paired Yaffo device
instead of a cloud service or a local folder: "Copy new photos from *Mom's laptop ›
Vacation 2026* into *Pictures › Imports › Mom*", on a schedule or when that device
comes online.

Sending to a device is out of scope. It is not library replication (two-way sync,
conflict resolution, people and album mirroring). [P2P Device Sharing](p2p-sharing.md#decisions-taken-deliberately) lists
that as a separate, larger design.

## Why it fits the sync engine

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

## Process model: the P2P engine stays in the web process

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

## Internal P2P API

### Transport and security

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

### Errors

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

### Resources

#### Status

```
GET /internal/p2p/v1/status
→ {"available": true, "device_id": "LRSU-BYMN-…", "hub_connected": true}
```

From `P2PService.identity` and `hub_connected`.

#### Devices

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

#### Scopes

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

#### Files

```
GET /internal/p2p/v1/devices/{device_id}/files?scope=album:12&cursor=200&limit=200[&session=…]
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

#### Sessions

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

#### Pulls

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

### What stays out of the API

Pairing, grants, revocation, renaming devices, and previews stay UI routes. A
worker never needs them, and keeping them out means nothing that gets past the
request checks can change trust or sharing. The API can read the list of paired
devices and pull from a granted scope, and that's all.

### The same checks for the whole app

The rest of the app's routes have no `Host` or origin check today. The app-wide
fix (a `Host` allowlist and an origin check on every request that changes
something) is planned separately in
[Web Routes and Request Security](web-routes-and-request-security.md) and doesn't
depend on peer sync.

### Mapping to the current code

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

## Peer source (import)

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
    nothing with this one.
  - `allow_relay`: off by default, plus a budget in GB when it's on (see *Relay
    policy*).
- **Import root and layout** as for every source. *Keep the source's folders* uses
  the manifest's `relative_path`, taken relative to the scope.
- **Consent:** "Copy new photos from *Mom's laptop › Vacation 2026* into
  *Pictures › Imports › Mom*." The device, scope, import root, and relay setting
  are part of the consent hash.

`SyncConfig.connection_key` stays `None` (like the Folder source). The device is an
option (`device: {id: device_id, label: display_name}`), so it's covered by
validation and consent like any option.

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
that, the Sharing page warns when a grant's scope contains a peer sync's import
root.

## Relay policy for unattended runs

Relay egress is the hub's only metered cost, and an unattended nightly backfill is
exactly the traffic that policy was written to discourage. Batches in the remote
gallery stay as they are (a person is watching and can continue anyway).
Scheduled peer syncs:

- default to `relay: never`, so they only run on the LAN or after a successful
  upgrade to a direct path, and otherwise end with "waiting for a direct
  connection";
- can opt in per sync to relayed transfers with a per-run byte budget (default
  1 GB, `RELAY_BATCH_BUDGET_BYTES`), throttled as today;
- show the path each run used (LAN / direct / relay) in the run summary.

## Triggers

- **Schedule:** nightly by default, like other sources.
- **`device_online`:** a new domain event, emitted by the web process when a paired
  device goes from absent to present, either on the hub (presence polled through
  `connected_device_ids` every few minutes) or on the LAN (an mDNS service-added
  callback). Payload `{"device_id": …, "path": "hub" | "lan"}`. A per-trigger
  `config.device_id` filter works like `album_changed`'s `album_id` filter, and the
  settle window (*Coalescing bursts* in the sync doc) absorbs a device that flaps.
  Peer syncs own this trigger by default, so a laptop that's opened once a week
  imports when it's opened, not on the first nightly run after.

## Wire protocol changes

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

## Prerequisites

Unattended, recurring transfers raise the stakes on the P2P doc's outstanding
hardening, so these come first:

- the `aioquic` canary test for the private `_peer_certificate` access (the one
  silent failure mode);
- timeouts retuned against real NATs instead of loopback values;
- serving-side rate limiting per peer;
- the two-machine LAN run with `zeroconf` packaged.

## Implementation phases

1. **Hardening** (above).
2. **Internal API.** The `PeerPuller` / `TransferManager` split, the
   `/internal/p2p/v1` blueprint (loopback caller, `Host`, and header checks;
   discovery through the heartbeat), sessions, and pulls. Tests: route tests
   against a fake service, including each request check, plus the existing
   two-instance loopback harness driving a session and a resumed pull through the
   API.
3. **Protocol 2:** the version field and `sha1` in manifests.
4. **Peer source:** handler, device step, scope picker, relay option,
   deterministic temp names and the `sweep_temps` change, read-ahead, and the
   `device_online` event and trigger.
   - **Exit:** a 2,000-photo scope imports across two runs with a restart in
     between, the second run imports only new files, and a mutual import moves no
     duplicate bytes.
5. User guide, translations, and Playwright specs once the flows settle, using the
   two-instance sharing sandbox.

## Open questions

- **Album scopes and local albums.** An import of a peer's album could also add the
  imported photos to a local album of the same name. It's local-only and needs no
  people mapping, so it's cheap. Is that wanted by default, or as an option?
- **Revocation mid-run.** The peer re-decides per request, so the next pull fails
  with `file_refused` or `scope_not_shared`. Should the run then pause, or finish
  the page and pause?
- **Several syncs from one device.** Sharing a session per peer is in the design;
  should their runs also be serialized per peer, so two nightly imports don't
  compete for the same connection?
