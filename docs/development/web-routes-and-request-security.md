# Web Routes and Request Security

**Status: implemented** (2026-10-10). The sections below describe the state before
the change and the plan as carried out; the conventions now live in
[Coding Standards — Web Routes and the API](coding-standards.md#web-routes-and-the-api).
Two things differ from the survey below: the app already had a session CSRF token
(`yaffo/security.py`, enforced outside `TESTING`), which stays alongside the new
checks, and the folder picker's `/api/fs/list` and `/api/fs/create-folder` weren't
covered by the plan and are unchanged.

Four changes, in order:

1. An app-wide request check that refuses DNS-rebinding and cross-site requests.
2. Opening files and folders on this computer by id, never by a path from the
   browser.
3. A small `/api/` blueprint holding only the resources that several pages share
   and that don't depend on any one page's UI.
4. Every other route that's under `/api/` today moves under the page that uses it.

The API (step 3) is designed as **resources**: nouns in the path, the HTTP method
saying what happens to them. Page routes (step 4) keep whatever is simplest for the
page's own UI. Each step is a separate change. Step 1 doesn't depend on the others
and should ship first.

## What's there today

The web UI has no login: anyone who can send the server a request is treated as
the user. Nothing in the server tells requests from Yaffo's own pages apart from
anything else.

- **112 routes change something** (POST, PUT, PATCH, DELETE). 18 are under `/api/`;
  the rest are under `/utilities` (29), `/sharing` (13), `/settings` (13), `/pages`
  (10), and others. `/api/` is a naming habit, not a boundary: some `/api/` routes
  read form fields, and plenty of page routes read JSON.
- **The front end calls them three ways:** about 50 HTMX requests
  (`hx-post`/`hx-put`/`hx-delete`), 25 plain `<form method="post">`, and about 60
  `fetch` calls.
- **No shared check runs on any of them:** no `Host` check, no origin check, no CSRF
  token.

Any web page the user visits can make the browser send requests to
`127.0.0.1:<port>`. Two ways matter:

- **Cross-site requests.** A plain form post from another site goes through. The
  JSON routes use `request.get_json(silent=True) or {}`, so a `text/plain` post
  isn't parsed, but the route still runs with an empty payload. These work today
  from any website:

  | route | effect |
  |---|---|
  | `POST /api/assistant/conversations/<id>/plans/<id>/approve` | applies an assistant change plan that doesn't ask for a typed count; plan ids are small integers |
  | `POST …/plans/<id>/decline`, `…/undo` | declines a plan, or undoes an applied one |
  | `POST /api/assistant/conversations/delete-all` | deletes every assistant conversation |
  | `POST /api/assistant/conversations/<id>/cancel` | stops a running answer |
  | `POST /api/media/<id>/reindex` | re-indexes a photo, dropping its person assignments |
  | `POST /api/media/<id>/favorite` | toggles favorite and fires `media_modified`; with *Export photo tag* on, that writes into the photo file |
  | form routes such as `/settings/llm/api-key`, `/sharing/devices/<id>/grants` | replace an API key; create or revoke a share grant |

- **DNS rebinding.** A page on `evil.example` that switches its DNS to `127.0.0.1`
  is same-origin with the server as far as the browser is concerned. It can send
  JSON and custom headers and **read the responses**, so every route is exposed,
  including:
  - `POST /api/open-file` and `/api/open-folder`, which open **any existing path**
    with the OS default app (`open` on macOS). Opening a script or an app bundle
    that way can run it;
  - `POST /api/settings/media-dirs` (`mkdir -p` anywhere, then index it) and
    `/api/settings/thumbnail-dir` (move every thumbnail to any folder);
  - every GET that returns library data.

Two smaller things turned up while surveying:

- `yaffo/static/media/view.js` hardcodes `/api/open-file`, `/api/open-folder`,
  and `/api/media/<id>/reindex` instead of using `APP_CONFIG`, against the coding
  standards.
- The Faces nav link in `base.html` checks `request.endpoint` against
  `faces_assign` and `faces_remove`. `faces_assign` is now the face-assignment
  API's endpoint, and `faces_remove` doesn't exist.

## Step 1: an app-wide request check

One `before_request` hook in `yaffo/app.py`, applied to every route.

**`Host` allowlist** (all methods). The request's `Host` hostname must be a
loopback name (`127.0.0.1`, `localhost`, `::1`) or one of the host names
configured for a server bound beyond loopback. Otherwise `400`. Only the hostname
is compared, not the port: a DNS-rebinding page controls the name, not the port,
and Flask's test client sends `localhost` with no port.

- The configured names come from a new `web.allowed_hosts` list in `config.toml`,
  with `YAFFO_WEB_ALLOWED_HOSTS` as the environment override, for containers.
- The docs-capture container reaches the sandbox by a container host name, so its
  sandbox configuration has to add that name.

**Origin check** (POST, PUT, PATCH, DELETE only). Refuse with `403` when either:

- `Sec-Fetch-Site` is `cross-site` or `same-site`; or
- an `Origin` header is present and doesn't equal the request's own
  `scheme://host:port`.

Current browsers send these headers on cross-site requests of every kind:
`fetch`, HTMX, and plain form posts. That covers all three ways the front end
calls the server without changing any template or script. `same-site` matters:
`127.0.0.1:5002` and `127.0.0.1:5003` (the two-instance sharing sandbox) are the
same site but different origins.

Requests with neither header pass. They come from non-browser clients (a sync
worker, `curl`, the test client), which no web page can make the browser imitate.
The internal P2P API keeps its own stricter checks (loopback caller and the
`X-Yaffo-Internal` header; see [Peer sync](automation-sync-adapters.md#peer-sync)).

A CSRF token was considered and dropped. It would mean changing every form, HTMX
call, and `fetch`, and it adds nothing over the origin check for the threat that
matters here (web pages in the user's browser).

**Tests:** route tests for each rule: a bad host, a cross-site POST, a same-site
POST from another port, a same-origin POST, and a header-less POST. Check that the
existing route suite still passes unchanged, and run the Playwright suites against
the sandbox.

## Step 2: opening files and folders by id

Replace `/api/open-file`, `/api/open-folder`, and `/api/assistant/open` with one
endpoint that takes ids, `POST /api/openings` (step 3). It reuses what
`/api/assistant/open` already does:

- The body is a `FileTarget` (`yaffo/site_agents/assistant/file_targets.py`):
  `{"show": "file" | "folder", "media_item_id": 41}`, or
  `{"show": "folder", "media_dir_id": "…", "path": "2024/July"}`.
- `resolve_target` looks the path up again and requires it to be inside a
  configured media folder.
- `show: "folder"` on a file reveals the file in its folder (`open_in_os(...,
  reveal=True)`).

The media page sends the photo's id instead of its path, through `APP_CONFIG`
instead of a hardcoded URL. No route accepts a raw path to open any more.

## Step 3: the API

### What belongs in it

A resource is in the API only if both are true:

- **More than one page uses it.** A JavaScript module counts once for each page
  that loads it.
- **It's independent of any page's UI.** It's library data (photos, faces, tags,
  places) and returns plain data, not markup or a shape built for one page.

Everything else is a page route (step 4).

### Conventions

These apply to the `api` blueprint only.

- **Paths name resources.** Collections are plural nouns (`/api/faces`); one member
  is the collection plus its id (`/api/media/41`); things that belong to a member
  nest under it (`/api/tags/Keywords/values`). No verbs in paths.
- **The method says what happens:**

  | method | meaning | idempotent |
  |---|---|---|
  | `GET` | read; never changes anything | yes |
  | `POST` to a collection | create a member; `201` with it, or `202` when the work is queued | no |
  | `PUT` | replace a resource, or set a single value, with the body | yes |
  | `PATCH` | change the given fields, leave the rest | yes, as used here |
  | `DELETE` | remove; `204`. Removing something already gone is also `204` | yes |

- **Actions become state or new resources.** An operation that sounds like a verb
  is modeled as the field it changes (favoriting sets a photo's `favorite`;
  assigning sets faces' `person_id`) or the record it creates (opening a file
  creates an opening).
- **Batches are a PATCH of the collection** with the member ids:
  `{"ids": [12, 13], "person_id": 7}` sets `person_id` on those members.
- **JSON in and out.** A request with a body must be `Content-Type:
  application/json`, otherwise `415`. That's a second, independent barrier
  against cross-site form posts.
- **One error shape**, the one most routes use today:
  `{"error": "<translated message>", "code": "<stable code>"}`, with a matching
  status (`400` invalid, `404` not found, `409` conflicts with the current state,
  `415` not JSON). Face assignment's `{"success": false, "message": …}` goes away.
- **No version in the path.** Every client is served by the same server build, so
  the server and its callers can't disagree about the shape.
- **Endpoint names** are `api.<resource>_<role>`, which `APP_CONFIG` exposes as
  `api_<…>`. Callers use `APP_CONFIG`, never hardcoded paths.

### The resources

| resource | method and path | body → response | replaces | used by |
|---|---|---|---|---|
| a photo | `PATCH /api/media/<id>` | `{"favorite": true}` → `{"id": 41, "favorite": true}` | `POST /api/media/<id>/favorite` (a toggle) | Gallery grid, media page |
| faces | `PATCH /api/faces` | `{"ids": [...], "person_id": 7}` · `{"ids": [...], "person_id": null}` · `{"ids": [...], "status": "ignored"}` → the updated faces | `POST /api/faces/assign`, `POST /api/faces/unassign` | Faces page (assign, ignore), media page (reassign, unassign) |
| a tag's values | `GET /api/tags/<name>/values` | → `{"tag": "Keywords", "values": [...]}` | `GET /api/tag-values?tag_name=` | the filter panel's tag filter: Gallery, Albums › Add, Locations, the sync filter. The remote gallery passes its own peer proxy URL instead |
| places | `GET /api/locations?q=…` | → `{"results": [...]}` | `GET /api/location-autocomplete` | the filter panel's location filter on the same pages, plus the remote gallery |
| openings | `POST /api/openings` | `FileTarget` → `202` | `/api/open-file`, `/api/open-folder`, `/api/assistant/open` | media page, the assistant |

Notes on the choices:

- **Favorite is a field of the photo,** so it's a PATCH of the photo with the
  wanted state, not a toggle. A toggle isn't idempotent: a retried request or a
  double click flips it back. The caller already knows the state it shows. Later
  photo fields (rating, caption) can join the same PATCH when a second page needs
  them.
- **Assigning, unassigning, and ignoring are changes to faces' fields** (`person_id`,
  `status`), so they're one PATCH of the faces collection. Unassigning alone is
  used only on the media page, but it's the same change as assigning with
  `person_id: null`. The current rules stay: assigning requires unassigned faces,
  unassigning requires assigned ones, otherwise `409`.
- **A tag's values are a sub-collection of the tag.** The name is URL-encoded in
  the path, and the route uses Werkzeug's `path` converter so a name containing
  `/` still matches.
- **Opening a file is an action on the desktop, not a change to a resource,** so
  it's modeled as creating an *opening*: a request to show a file or folder on
  this computer. It's create-only (there's nothing to GET back) and answers `202`,
  because the OS opens the file after the response.

## Step 4: page routes

Routes used by one page, or tied to its UI, move under that page's prefix and take
its endpoint naming (`<area>_<thing>`). Their methods and bodies stay as they are:
nothing about them is meant for other callers, so the simplest convention is the
one the page's script already uses. Because scripts look routes up by endpoint
name, a moved route whose endpoint name doesn't change needs no script change at
all.

### Assistant

All of these are used only by the assistant component (`assistant.js`), on the
Ask Yaffo page and in the panel on every page. They move from `/api/assistant/…`
to `/assistant/…`, next to the page itself. Endpoint names already follow the
pattern (`assistant_*`) and don't change, so `assistant.js` needs no change for
these.

| from | to |
|---|---|
| `GET`, `POST /api/assistant/conversations` | `GET`, `POST /assistant/conversations` |
| `GET`, `PATCH`, `DELETE /api/assistant/conversations/<id>` | `GET`, `PATCH`, `DELETE /assistant/conversations/<id>` |
| `POST /api/assistant/conversations/<id>/messages` | `POST /assistant/conversations/<id>/messages` |
| `POST …/plans/<plan_id>/approve`, `/decline`, `/undo` | `POST /assistant/conversations/<id>/plans/<plan_id>/approve`, `/decline`, `/undo` |
| `POST /api/assistant/conversations/<id>/cancel` | `POST /assistant/conversations/<id>/cancel` |
| `POST /api/assistant/conversations/delete-all` | `POST /assistant/conversations/delete-all` |
| `POST /api/assistant/open` | `POST /api/openings` (step 2) |

### Media page

| from | to | endpoint |
|---|---|---|
| `POST /api/media/<id>/reindex` | `POST /media/<id>/reindex` | `media_reindex` (was `reindex_media_item`) |
| `PUT /api/media/<id>/tags` | `PUT /media/<id>/tags` | `media_tags_update` (was `update_media_tags`) |
| `POST /api/open-file`, `/api/open-folder` | `POST /api/openings` (step 2) | `api.openings_create` |

### Faces page

| from | to | endpoint |
|---|---|---|
| `POST /api/people/create` | `POST /faces/people/create` | `faces_person_create` (was `api_people_create`) |
| `POST /api/faces/assign`, `/unassign` | `PATCH /api/faces` (step 3) | `api.faces_update` |

The People page's own `POST /people/create` (a form) is unchanged. The Faces
page's version returns JSON for its "new person" dialog, so it stays the Faces
page's.

### Settings page

| from | to | endpoint |
|---|---|---|
| `POST /api/settings/media-dirs` | `POST /settings/media-dirs` | `settings_media_dirs_add` (was `add_media_dir`) |
| `DELETE /api/settings/media-dirs/<index>` | `DELETE /settings/media-dirs/<index>` | `settings_media_dirs_remove` (was `remove_media_dir`) |
| `GET /api/settings/thumbnail-stats` | `GET /settings/thumbnail-stats` | `settings_thumbnail_stats` (was `get_thumbnail_stats_api`). Its streaming sibling is already `/settings/thumbnail-stats/stream` |
| `POST /api/settings/thumbnail-dir` | `POST /settings/thumbnail-dir` | `settings_thumbnail_dir` (was `update_thumbnail_dir`) |

Media dirs are removed by list position. They have stable GUIDs now
(`media_dir_id`, already sent to the settings page), and removing by GUID would
stop a stale page from removing the wrong folder. That's a separate small fix and
isn't required by this plan.

### Routes that stay where they are

Three automation routes are used from two templates each
(`automations_run_now`, `sync_choices`, `sync_test`), but both templates are
pages of the same automations area and the responses are built for them. They
stay page routes, unchanged.

### What has to change with the moves

- **JavaScript**, only where an endpoint name, method, or body changes:
  - `media/favorite.js`: `api_media_update`, PATCH with the wanted state;
  - `media/face-reassign.js`, `faces/index.js`: the faces PATCH; `faces/index.js`
    also the new person endpoint;
  - `media/view.js`: openings, `media_reindex`, and its hardcoded URLs;
  - `media/tags.js`: `media_tags_update`;
  - `assistant.js`: openings only;
  - `settings/index.js`: the renamed settings endpoints;
  - `filters/tags.js`, `filters/location-autocomplete.js`: the API paths.
- **`base.html`:** the Faces nav check drops the stale `faces_assign` and
  `faces_remove`.
- **Tests:** Python route tests that use literal paths, `tests_js` mocks of
  `APP_CONFIG.urls`, and the Playwright specs and progress notes (per the
  frontend test-suite rules).
- **Docs:** the `APP_CONFIG` section of the coding standards uses
  `faces_assign` as its example; update it, and add the API criteria and
  conventions from step 3.
- **No translation changes** beyond the error messages that move from face
  assignment's `message` field to `error`.

## Order

1. **Request check** (step 1), with its tests. This is independent, and it
   closes the cross-site and DNS-rebinding exposure for every route at once.
2. **Openings by id** (step 2): the one change with an exposure of its own
   (opening arbitrary paths) beyond what step 1 closes.
3. **The `api` blueprint** (step 3), moving the five shared resources.
4. **Page routes** (step 4), area by area: assistant, media, faces, settings.
