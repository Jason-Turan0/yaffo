# Automation Web Calls — Authorized Outbound HTTP for Custom Automations (Design Sketch)

**Status: proposed, not built.** This captures the design so the first concrete
use case (sharing selected photos into a Google Photos album) can be built on a
general capability instead of a one-off system automation. Checked against
Google Photos, Microsoft OneDrive, Apple iCloud Photos, and Immich (see
*Provider fit*): three fit, Apple needs a different capability. See
[Automations](automations.md) for the existing architecture this extends.

## Motivation

The driving use case:

```
Phone ──Syncthing──► SD card (master library)
                        │
                        ▼
                     Yaffo: index, face-tag, label, pick "share with Mom"
                        │  custom automation (this doc)
                        ▼
          Google Photos album "For Mom" (created by the automation)
                        │  shared once, by the user, in the Google Photos app
                        ▼
                     Mom views the album any time
```

Google Photos can't do this on its own here: face groups are unavailable in
Illinois/Texas, and since 2025-03-31 the Photos Library API only lets an app
manage media and albums **it created** (no sharing endpoint, no reading the
user's library). Uploading into an app-created album that the user shares once
still works. rclone's Google Photos backend is deprecated (Tier 5) and Drive for
desktop's Photos backup ended 2026-08-10, so the upload step has to be Yaffo's.

It should be a **custom** automation, not a system automation:

- The selection rule is personal ("faces include Mom or the kids, last 30 days,
  no screenshots or duplicates") and is exactly what the AI builder is good at.
- Other destinations (another album, a photo-frame service, a family website)
  should be the same feature with a different script, not new system handlers.

But custom automations are **hermetic Starlark with no I/O**: every effect goes
through a `HostFunction` in `automation_host.py`. So the capability is a small set
of new host functions plus an **authorization model**, so that a script can only
reach the web endpoints the user explicitly approved.

## Principles

1. **No ambient network.** Starlark stays hermetic. The only way out is the
   web host functions below; every call is checked against the automation's
   grants at the host boundary, which remains the trust boundary.
2. **Detect, then ask.** The set of endpoints a script uses is detected from its
   code (static) and confirmed by the test run (dynamic). Publishing shows that
   set and requires the user to authorize it. Nothing is authorized implicitly.
3. **Code changes re-open consent.** If a new draft adds or widens an endpoint,
   publishing it requires re-authorization. Narrowing does not.
4. **Scripts never see secrets.** Credentials live in a **Connection** (OAuth
   tokens, API keys) stored by Yaffo. Scripts name a connection; the host attaches
   auth headers. Tokens never appear in script inputs, outputs, logs, or previews.
5. **Scripts never read files.** File bytes are uploaded by the host from a
   `media_item_id`, so a web-calling script can send only indexed photos, by id.
6. **Failures are data**, as today: a denied, failed, or rate-limited call
   returns an error value; it never crashes the worker.

## Concepts

### Connection

A named, user-created credential for one remote service, managed on a new
**Connections** utility page.

| field | notes |
|---|---|
| `slug` | stable name scripts refer to, e.g. `google_photos` |
| `kind` | `oauth2` (authorization-code + PKCE, desktop/loopback redirect) or `api_key` |
| `base_urls` | the hosts this connection may be used against, e.g. `https://photoslibrary.googleapis.com` |
| `scopes` | for OAuth: requested at sign-in, shown to the user |
| secret material | refresh/access token or key, stored encrypted (OS keychain or an encrypted settings blob), never exposed to scripts |

The user creates and signs in to a connection themselves (the OAuth consent
happens in their browser). An automation can't create or modify connections.

### Endpoint grant

An authorization for one automation to call one **endpoint pattern** through one
connection:

```
(automation_id, connection_slug, method, url_pattern, granted_at, granted_code_hash)
```

`url_pattern` is a URL template with typed placeholders, e.g.
`POST https://photoslibrary.googleapis.com/v1/albums/{album_id}:batchAddMediaItems`.
Placeholders match one path segment; query strings are matched by key set.
Grants are per automation; two automations using the same connection authorize
separately.

### Automation state

Exporters need memory between runs (which photos were already uploaded, the
album id). Add a small per-automation key/value store so the script doesn't need
I/O for it:

- `state_get(key)`, `state_set(entries)` (batch), `state_delete(keys)` (batch).
- JSON values, size-capped. Preview runs read real state and record writes
  without performing them, like other mutations.

## Host API additions

| function | kind | does |
|---|---|---|
| `web_request(connection, method, url, json=None, headers=None)` | mutating unless `GET`/`HEAD` | Authorized JSON request. Host checks the grant, attaches auth, enforces timeouts/size caps, returns `{status, json, headers}` (allowlisted headers only). |
| `web_upload_media(connection, method, url, media_item_id, encoding="raw", field=None, fields=None, headers=None)` | mutating | Sends the photo's **file bytes** (by id, server-side read). `encoding`: `"raw"` (body is the file), `"multipart"` (file in form `field`, plus string `fields`), or `"session"` (chunked PUTs to an upload URL from a prior response; see *Provider fit*). Returns `{status, json, text, headers}`. This is how a script uploads a photo without file access. |
| `state_get(key)` / `state_set(entries)` / `state_delete(keys)` | read / mutating | Per-automation key/value memory (see above). |

Both web functions are registered in `HOST_API` like every other capability, so
`render_host_api()` documents them for the builder prompt and the recording
variant gets them for free. They use the registry's existing **network** metadata
(today used only for reviewed assistant plans) and become the first functions
where that metadata gates execution.

**Static-analysis constraint:** `connection`, `method`, and the URL's scheme +
host + path template must be **string literals** (or module-level constants built
from literals). Placeholder values may be dynamic, written as
`url_template("https://…/albums/{album_id}:batchAddMediaItems", album_id=x)`.
This keeps detection complete: if the detector can't resolve a call's endpoint,
publish is blocked with a pointer to the offending line.

## Detection

Two passes, both reusing existing machinery:

1. **Static (at write-code time).** `write_automation_code` already parse-checks
   with `validate_starlark`. Add a pass over the AST that collects every
   `web_request` / `web_upload_media` call site into an **endpoint manifest**:
   `[{connection, method, url_pattern, line}]`. Non-literal endpoints become
   errors returned to the builder model, so it fixes them before the user sees
   the draft. The manifest is stored with `working_code`.
2. **Dynamic (Test run).** The preview harness already records every host call.
   Web calls in preview are **recorded, not sent** (even GETs by default; see
   Open questions), and each recorded call is matched against the static manifest.
   A call outside the manifest would be a detector bug and fails the test loudly.
   The Actions table shows them like other actions: "Would upload IMG_1234.jpg to
   Google Photos", "Would add 12 photos to album *For Mom*".

## Authoring experience

Authorization is part of building the automation, not a separate settings page:

1. **Chat.** The user asks: "Every Sunday, upload new photos of Mom and the kids
   to my Google Photos album *For Mom*, with a caption of who's in it and where."
   The builder prompt now includes the web host functions and the user's
   **available connections** (slugs + base URLs + scopes, never secrets). If no
   suitable connection exists, the model says so and links to Connections rather
   than writing a script that can't run.
2. **Draft.** On each code write, the detail page shows a **Web access** panel
   next to the code: the endpoint manifest grouped by connection, each line with
   method, URL pattern, and a plain-language summary ("Upload a photo",
   "Create an album", "Add photos to an album"). New or widened endpoints since
   the last published version are highlighted.
3. **Test.** The dry run shows the recorded web calls in the Actions table.
4. **Publish = authorize.** If the draft's manifest includes any endpoint not
   already granted, **Publish** opens an authorization modal listing exactly
   those endpoints, the connection they use, and whether each is a read or a
   write. The user ticks to approve (all or nothing; there's no partial publish).
   On confirm, `automation_repository.publish` writes the grants with the
   published code's hash in the same transaction.
5. **Revoke.** The Web access panel on a published automation has **Revoke** per
   endpoint or per connection. Revoking disables the automation's next runs (they
   fail with "web access revoked") until the user re-publishes.

System automations don't need this: their handlers are reviewed code.

## Runtime enforcement

At every web host call, the host checks, in order:

1. The automation is published and enabled, and its `published_code` hash matches
   the grants' `granted_code_hash` (defense against code changed without
   re-authorization).
2. A grant exists for `(connection, method)` whose `url_pattern` matches the
   resolved URL, and the URL's host is in the connection's `base_urls`.
3. The connection exists, is signed in, and its token refreshes.
4. Per-run limits: max web calls (separate from the 1,000 host-call cap), max
   upload bytes, per-call timeout (host-call I/O deadlines are already flagged as
   deferred in [Automations](automations.md); web calls must set their own), and
   response size cap.

Denials return `{status: 0, error: "…"}` to the script and are logged on the run's
Job, so run history shows "Denied: POST …/albums (not authorized)".

Redirects are followed only within the connection's `base_urls`. Responses
expose an allowlist of headers (no `set-cookie`, no auth echoes).

## Worked example: "For Mom" album

Connection `google_photos`: OAuth2, base URL `https://photoslibrary.googleapis.com`,
scopes `photoslibrary.appendonly` + `photoslibrary.edit.appcreateddata`. The user
signs in once from Connections.

Detected manifest (what the publish modal would show):

| method | endpoint | meaning |
|---|---|---|
| POST | `/v1/albums` | Create the album (first run only) |
| POST | `/v1/uploads` | Upload a photo's bytes (`web_upload_media`) |
| POST | `/v1/mediaItems:batchCreate` | Add uploaded photos to the album, with captions |
| POST | `/v1/albums/{album_id}:batchRemoveMediaItems` | Remove photos that were un-tagged |

Sketch of the script's flow (Starlark, abbreviated):

```python
album_id = state_get("album_id")
if not album_id:
    r = web_request("google_photos", "POST",
                    "https://photoslibrary.googleapis.com/v1/albums",
                    json={"album": {"title": "For Mom"}})
    album_id = r["json"]["id"]
    state_set([{"key": "album_id", "value": album_id}])

uploaded = state_get("uploaded") or {}          # media_item_id -> google mediaItemId
picks = data_query(...)                          # faces include Mom/kids, recent, not screenshots
new = [p for p in picks if str(p["id"]) not in uploaded]

items = []
for p in new:
    up = web_upload_media("google_photos", "POST",
                          "https://photoslibrary.googleapis.com/v1/uploads", p["id"],
                          headers={"X-Goog-Upload-Protocol": "raw"})
    items.append({"description": caption_for(p),
                  "simpleMediaItem": {"uploadToken": up["text"], "fileName": p["file_name"]}})
# batchCreate in chunks of 50 with albumId, then record returned ids in state
```

The user shares *For Mom* with their mom once, in the Google Photos app. After
that, every run's additions appear for her.

Things to verify with a spike before building (see the Personal Agentic Agent
project notes): adding to an app-created album **after** it has been shared;
whether the `description` shows as the caption in the shared view; whether
re-uploading a photo already in the user's library creates a duplicate item
(if so, consider archiving uploads); removal via `batchRemoveMediaItems`.

## Provider fit (rule of three)

A generic capability should hold for at least three providers before it's built.
The "upload picks to an album someone else can view" use case, checked against
the big three photo clouds plus one self-hosted option:

| | Google Photos | Microsoft OneDrive (personal) | Apple iCloud Photos | Immich (self-hosted) |
|---|---|---|---|---|
| Public web API | Photos Library API | Microsoft Graph | **None** for Photos | REST API |
| Auth | OAuth 2.0 (Google) | OAuth 2.0 (Microsoft identity, `consumers` tenant) | n/a | API key header (`x-api-key`) |
| Create album | `POST /v1/albums` (app-created only) | `POST /drive/bundles` with `"bundle": {"album": {}}` | n/a | `POST /api/albums` |
| Upload a photo | `POST /v1/uploads` (raw bytes) → token → `mediaItems:batchCreate` | `PUT /me/drive/items/{parent}:/{filename}:/content` (small) or `createUploadSession` → chunked `PUT` to returned `uploadUrl` (large) | n/a | `POST /api/assets` (multipart) |
| Add to album | in `batchCreate` (`albumId`), or `albums/{id}:batchAddMediaItems` | `children` on bundle create, or `POST /drive/bundles/{id}/children` | n/a | `PUT /api/albums/{id}/assets` |
| Caption | `description` per item | item `description` | n/a | asset `description` |
| Share with Mom via API | **No** (sharing scope removed 2025); user shares once in the app | **Yes**: `POST /items/{id}/createLink` or `/invite` | **No** (no API for Shared Albums; PhotoKit can't add to them) | **Yes**: shared links / album users |
| Fits this design? | ✓ | ✓ with additions below | ✗ (needs a different capability) | ✓ |

**Google, Microsoft, and Immich clear the rule of three** for authorized web
calls. Generalizing past Google adds these requirements to the design:

1. **Upload encodings.** Raw body (Google), path-addressed `PUT` (Microsoft small
   files), multipart form (Immich), and **chunked upload sessions** (Microsoft
   large files; Google's resumable protocol too). Hence `web_upload_media`'s
   `encoding` parameter. Chunking stays in the host; the script only passes the
   session URL.
2. **Follow-up URLs on other hosts.** Microsoft's `createUploadSession` returns an
   `uploadUrl` on a different host (e.g. `*.sharepoint.com` / `api.onedrive.com`),
   and some calls return a `Location` to poll. A connection therefore declares
   **`followup_hosts`** in addition to `base_urls`, and a grant can be of kind
   **"URL returned by granted endpoint X"**: the host only accepts a follow-up URL
   that it itself received from a granted call in the same run (tracked
   server-side, not trusted from the script).
3. **Path-addressed URL patterns.** Graph addresses items by path with `:` segments
   (`/me/drive/root:/Pictures/For Mom/{filename}:/content`). Placeholders need
   types: `{id}` (one segment), `{filename}` (one segment, URL-encoded by the host),
   `{path}` (multiple segments). The static detector records the type.
4. **Provider-specific OAuth.** Connections need configurable authorization/token
   endpoints and scope lists (Google vs. Microsoft identity platform), plus an
   **API key** kind (Immich). Ship provider presets (Google Photos, OneDrive,
   Immich) so users don't type endpoints.
5. **Sharing is a risk class of its own.** Where an API *can* share (Microsoft,
   Immich), that endpoint sends data to other people. The registry's existing
   risk metadata should mark such endpoints **"shares outside your account"**;
   the publish modal lists them separately with their own confirmation, and the
   builder prompt should prefer the "user shares once, manually" pattern unless
   the user explicitly asks for automated sharing.

**Apple is a different capability.** iCloud Photos has no web API, and PhotoKit
cannot add to Shared Albums, so nothing here can reach a shared iCloud album. The
closest options are local, not web: a macOS bridge (Photos.app scripting or a
small PhotoKit helper) that imports picks into a **regular** Photos album, after
which the user drags them into the Shared Album; or Yaffo exporting picks to a
folder. That would be a separate "local app bridge" capability with its own
permission model (macOS Photos access), not part of web calls. Out of scope here.

## Data model sketch

- `connections` — `id, slug, kind (oauth2|api_key), provider_preset, auth_endpoints (JSON, oauth2), base_urls (JSON), followup_hosts (JSON), scopes (JSON), secret_ref, created_at, last_used_at`.
- `automation_web_grants` — `id, automation_id, connection_id, grant_kind (endpoint|followup), method, url_pattern, followup_of (grant id, for followup kind), risk (read|write|shares_externally), granted_at, granted_code_hash`; unique on `(automation_id, connection_id, method, url_pattern)`.
- `automation_state` — `automation_id, key, value (JSON), updated_at`; PK `(automation_id, key)`.
- `automations.working_web_manifest` (JSON) — the detected manifest for the draft.

All changes follow the migration rules in [Project Context](project-context.md).

## Open questions

- **GETs in Test.** Should read-only `GET`s to already-granted endpoints run for
  real in Test so scripts can see live remote state? Default proposed: no; return
  a recorded placeholder like mutating calls, opt-in per test run.
- **Secret storage.** OS keychain (keyring) vs. an encrypted settings value; the
  distribution story (see [Distribution](distribution.md)) decides.
- **Grant granularity.** Per endpoint (proposed) vs. per connection with method
  classes (simpler UI, broader access).
- **Builder access to connections.** Should the builder model ever see more than
  connection slugs and scopes (e.g. a test call)? Proposed: no.
- **Rate limits.** Google Photos allows ~10,000 requests/project/day; Yaffo should
  surface 429s as run errors and back off rather than retry inside one run.
- **Reuse beyond automations.** The AI Page Builder and AI Assistant could use the
  same connections and grants later; keep the grant check in one module.
