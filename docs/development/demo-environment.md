# Demo Environment Plan

Status: Phase 0 accepted; Phase 1 in progress

Last reviewed: 2026-07-18

## Recommendation

Build the first Yaffo demo as an **anonymous, resettable sandbox** containing two
pre-paired Yaffo instances on one Compute Engine VM:

- `demo-a.yaffo.app` — the sharing device, seeded with a small licensed or
  synthetic library and prepared albums.
- `demo-b.yaffo.app` — the receiving device, seeded with a different library
  and an empty download directory.
- `demo.yaffo.app` — a static HTML walkthrough, served directly by Caddy, with
  links that open A and B in separate tabs.
- Caddy on the demo VM for automatic HTTPS, direct walkthrough-file serving, and
  routing of the A/B hostnames to their private containers.
- The existing `hub.yaffo.app` for P2P signaling, STUN, and encrypted relay
  fallback.
- `YAFFO_DEMO_MODE=1` on both app instances. This enables a centralized,
  fail-closed HTTP-method gate, immutable demo configuration, workload and
  transfer caps, and an explicit disposable-demo banner.
- A scheduled restore from golden data directories once each day, plus an
  operator-triggered reset and emergency stop.

This shape demonstrates real Yaffo behavior, including the actual device-key
identity and P2P transfer path, while keeping the initial hosting and operational
model understandable. It must not be presented as a multi-user or production
cloud edition of Yaffo.

The demo is intentionally reachable without a login. Its safety boundary is not
visitor identity; it is the deliberately restricted capability set exposed by
demo mode, backed by container isolation, rate and resource limits, disposable
data, and automatic reset. The unrestricted local application must never be
reachable from the public hostname.

## Goals and non-goals

The first demo should let a visitor:

1. Browse a realistic but non-sensitive photo and video library.
2. Browse filters, people, labels, locations, favorites, albums, and reviewed
   custom pages.
3. See that pairing and sharing grants are different concepts.
4. Open Device B, browse a share from Device A, preview remote media.

It is not intended to provide:

- storage for visitor uploads or real personal photos;
- multiple simultaneous, mutually isolated users;
- an availability or durability SLA;
- performance benchmarking of face recognition or bulk indexing;
- proof that NAT traversal works between two independent networks;
- unrestricted access to the AI builders or paid model APIs.
- Ability to transfer files between instances

The existing [`deploy/yaffo_peer`](../../deploy/yaffo_peer/README.md) topology is
the right tool for real-network and relay validation. It should remain separate
from the stable product demo.

## Anonymous access model

### Demo mode, not a login

Yaffo sharing correctly has no user accounts. A P2P device authenticates to the
hub and another device with its ECDSA keypair, and a human establishes trust with
a one-time pairing code. The public demo should preserve that accountless product
story instead of adding a visitor login.

The normal web app does assume that anyone who can reach its listening socket is
the device owner. The public deployment therefore needs a separate runtime
contract:

- `YAFFO_DEMO_MODE=1` must be read once at startup and cannot be changed through
  the UI or a request.
- A single application-wide request gate must reject `POST`, `PUT`, `PATCH`, and
  `DELETE` in demo mode unless the exact method, Flask endpoint name, and demo
  role appear in a small central exception set. A new mutation is therefore
  blocked without requiring a decorator or attribute on its route.
- Public `GET`, `HEAD`, and `OPTIONS` endpoints must come from a central allowlist.
  This separately excludes read-like routes that expose the filesystem,
  configuration, or expensive operations. HTTP method alone is not a sufficient
  safety classification.
- Blocked endpoints must fail in the Flask application, not merely disappear from
  templates or be blocked at the proxy.
- Dangerous service functions must also reject calls in demo mode when they can
  be reached by background jobs or another non-route path.
- `YAFFO_DEMO_ROLE=source|receiver` may narrow the policy further: A is the
  mostly-read-only sharing source; B is allowed bounded transfer controls.
- The UI must explain disabled controls rather than silently pretending those
  product capabilities do not exist.

This is not an authorization system and should not spread into the desktop
product as one. It is a deployment safety profile for a disposable public
instance.

### Public ingress and private operator access

Follow the existing `hub.yaffo.app` deployment pattern and keep the public path
small:

- Reserve one static external IPv4 address for the demo VM.
- Create `A` records for `demo.yaffo.app`, `demo-a.yaffo.app`, and
  `demo-b.yaffo.app` at the existing authoritative DNS provider. All three records
  point to that address.
- Run Caddy in the deployment and expose only TCP 80 and 443. Caddy obtains and
  renews public certificates, redirects HTTP to HTTPS, serves the walkthrough for
  `demo.yaffo.app`, and reverse-proxies the A/B hostnames to their private
  container services.
- Publish no wildcard DNS record and configure no catch-all application proxy.
  Requests for unexpected hostnames must not reach Yaffo.
- Keep the app ports on the private container network. They must never be bound
  to the VM's public interface. The walkthrough has no process or port of its own.

The public pilot deliberately has no separate CDN, managed WAF, or bot product.
Demo mode, bounded fixtures, server-side action policy, request and transfer
quotas, container limits, automatic reset, and an emergency stop remain the
safety boundary. Add another network service only if observed abuse demonstrates
a need that these controls do not address.

Keep reset, metrics, logs, SSH, and emergency controls off the public hostnames.
Operators use IAP TCP forwarding, which applies IAM checks before opening the
administrative connection. See the [IAP TCP forwarding
overview](https://docs.cloud.google.com/iap/docs/tcp-forwarding-overview).

## Topology

```text
anonymous browser
      |
      | HTTPS :443
      v
Caddy on demo VM static IP
      |
      +-- demo.yaffo.app --------> static walkthrough files
      |
      +-- demo-a.yaffo.app ------> Yaffo A web :5101
      |                            task system: off
      |                            /data/a only
      |
      +-- demo-b.yaffo.app ------> Yaffo B web :5102
                                   task system: off
                                   /data/b only

Yaffo A P2P UDP :5201 ----\
                            +-- WSS + QUIC/UDP --> hub.yaffo.app
Yaffo B P2P UDP :5202 ----/                signaling/STUN/relay
```

### Walkthrough page and serving

Keep the walkthrough as reviewed static files in the deployment source, for
example:

```text
deploy/demo/walkthrough/
├── index.html
├── walkthrough.css
└── walkthrough.js
```

Mount that directory read-only at `/srv/walkthrough` in the Caddy container. Do
not run Flask, Node, or a separate static container for it. The page needs no
database, cookies, server-side rendering, or API: it explains the shared and
resettable sandbox, links to A and B with `target="_blank"`, and presents the
numbered sharing walkthrough. A small local script may calculate the next reset
time from the published reset interval; it must not make the walkthrough an
operational control surface.

Use exact Caddy site blocks rather than a catch-all:

```caddyfile
demo.yaffo.app {
    root * /srv/walkthrough
    file_server
}

demo-a.yaffo.app {
    reverse_proxy yaffo-a:5101
}

demo-b.yaffo.app {
    reverse_proxy yaffo-b:5102
}
```

Add the security headers specified below to the walkthrough site, including a
same-origin-only Content Security Policy and `frame-ancestors 'none'`. Serve the
HTML with `Cache-Control: no-cache` so walkthrough or reset-schedule corrections
appear promptly; versioned CSS, JavaScript, and images may use long-lived cache
headers.

Use one `e2-medium` VM initially. Run the two Yaffo processes in separate
containers with distinct:

- `YAFFO_DATA_DIR` mounts;
- web and P2P ports;
- SQLite application and queue databases;
- media, thumbnail, temp, download, and log directories;
- P2P identity key storage;
- Flask `SECRET_KEY` values;
- CPU, memory, PID, and writable-volume limits.

Each container must see only its own data and media mounts. Mounting a shared
`/data` tree into both containers defeats the isolation because Yaffo includes a
local filesystem picker. Sharing must happen over Yaffo's P2P protocol, not
through a host directory visible to both containers.

One VM is enough to demonstrate the product workflow, but it does not prove NAT
traversal. Two app VMs should be used only for a guided network demonstration or
the existing Tier 2/3 transport tests. Two full app VMs approximately double
compute and disk cost and add no value to most visitors.

### Machine sizing

The demo should not run the task-queue host, task workers, filesystem watcher, or
periodic dispatcher. Indexing, face analysis, label classification, automations,
and AI generation are blocked in demo mode, while thumbnails and previews are
prepared in the golden fixture. The two always-on Yaffo processes therefore serve
HTTP and SQLite reads, plus P2P presence and browsing.

A local sizing probe on 2026-07-18 measured about 295 MB RSS after one web process
loaded all 144 routes, before request traffic. Two processes start near 600 MB;
Linux, Caddy, container overhead, SQLite page cache, Python allocation growth, and
media responses still require substantial headroom. This is enough evidence to
reject 1 GB, but not to reserve 16 GB without a deployment benchmark.

Current `us-central1` list-price options are:

| Machine type | Effective CPU | Memory | Compute/month | About 180 hours | Assessment |
|---|---:|---:|---:|---:|---|
| `e2-micro` | 0.25 sustained vCPU, short burst | 1 GB | ~$6, possibly covered by an unused Free Tier allotment | ~$1.50 | Too little memory and sustained CPU for two app processes |
| `e2-small` | 0.5 sustained vCPU, short burst | 2 GB | ~$12 | ~$3 | Could boot, but leaves little failure or traffic headroom |
| **`e2-medium`** | **1 sustained vCPU, bursts across 2 guest vCPUs** | **4 GB** | **~$24** | **~$6** | **Recommended pilot size with all background work disabled** |
| `e2-standard-2` | 2 vCPUs | 8 GB | ~$49 | ~$12 | Simple upgrade if shared-core CPU or 4 GB memory is insufficient |
| `e2-highmem-2` | 2 vCPUs | 16 GB | ~$66 | ~$16 | Memory-heavy alternative; no current demo need |
| `e2-standard-4` | 4 vCPUs | 16 GB | ~$98 | ~$24 | Conservative production/indexing size; excessive for the proposed demo |

Google documents the shared-core allocations and burst behavior in [E2 machine
types](https://docs.cloud.google.com/compute/docs/general-purpose-machines#e2_shared-core_machine_types).
Verify final prices with the [Google Cloud Pricing
Calculator](https://cloud.google.com/products/calculator).

Start with `e2-medium` and make `machine_type` a Terraform variable. Exercise two
simultaneous galleries, filters and detail views, one remote preview, one capped
transfer, reset, and the expected anonymous concurrency. Upgrade to
`e2-standard-2` if the VM swaps or approaches its memory limit, exhausts its
shared-core CPU entitlement, or misses the agreed latency target. Do not size for
indexing or model inference because those actions are absent from this deployment.

### Runtime changes needed

The checked-in [`deploy/gcp`](../../deploy/gcp/README.md) directory is an early
infrastructure scaffold, not a deployable demo. It creates a private COS VM,
Artifact Registry, and a disk, but it does not currently build or run Yaffo,
configure internet egress, expose public HTTPS safely, seed/reset data, or run a
second instance.

Before deploying, add:

1. A production container image for `linux/amd64`, with runtime assets prefetched
   during the build or first provisioning rather than downloaded on every reset.
2. A deployment entrypoint that runs only Waitress and the P2P service. It must
   not start the task-queue host, task workers, filesystem watcher, or periodic
   automation dispatcher, and it must not launch runtime asset-download threads
   because those assets are already in the image. The current `python -m yaffo`
   path starts those components, so it needs a demo-aware role switch; starting only
   `create_app()` under Gunicorn would omit P2P and is not sufficient either.
3. A `YAFFO_WEB_HOST`-style setting so Waitress can bind to a private container
   interface. It currently binds to `127.0.0.1`; never bind it directly to a
   public interface.
4. Per-instance ports and a configurable Waitress thread count, initially four
   threads per app. Do not express the disabled task system as zero workers: omit
   the host process entirely, and block job-producing routes before they enqueue.
5. A stable headless secret store for each P2P key. A dedicated file-backed
   keyring is acceptable for these disposable demo identities if its volume is
   isolated and mode `0600`; do not use it for real user identities. Keep identity
   keys outside the resettable data directory, because the pre-paired database
   snapshots refer to those identities.
6. A Caddy configuration for the three exact anonymous hostnames. Mount the
   checked-in walkthrough directory read-only and serve it directly for
   `demo.yaffo.app`; reverse-proxy only the A/B hostnames across the private
   container network. Expose only Caddy's TCP 80 and 443 ports on the VM, and
   persist Caddy's certificate state across container and VM restarts.
7. Scheduled start/stop, health checks, restore automation, budget alerts, image
   retention, and log retention.

## Demonstrating sharing

### Default self-service story

Pre-pair the two instances and seed a small number of grants. The walkthrough
should explain that pairing was performed once during provisioning and did not
grant access by itself.

Recommended fixture:

- Device A display name: `Family Mac`.
- Device B display name: `Travel Laptop`.
- Device A album: `Chicago Weekend`, containing media from more than one folder.
- Device A folder: `Trips/Chicago`.
- Device B download directory: `/data/downloads`.
- Active grants from A to B: the album and the folder, but not A's complete media
  directory.
- A few non-granted sibling files so the demo proves that an album or folder grant
  is scoped rather than equivalent to the whole library.

Walkthrough:

1. In A, open Sharing and show B as paired and online.
2. Show A's active folder and album grants. Trust and grant mutation controls are
   explanatory but disabled in the anonymous demo.
3. Open B in a second tab and select A under **Shared with me**.
4. Browse and filter the remote album, open a remote preview, and select two small
   files.
5. Pull them to B. Show progress and the path reported by the transfer panel.
6. Show where B received the files. Explain cancellation, resumption, and
   revocation with the walkthrough because public visitors cannot alter the
   shared transfer or trust state.

Do not promise that this single-host topology will show a particular network
path. Every call begins relayed and may upgrade; container networking and GCE NAT
determine what the UI reports. If the purpose of a session is specifically to
show relay fallback, use the `HARD` profile in `deploy/yaffo_peer`.

### Pairing demonstration

Pairing codes are short-lived, single-use, and both devices must be online. A
shared, pre-paired sandbox therefore cannot safely let concurrent visitors repeat
the pairing ceremony: one visitor can consume a code or revoke the peer for
everyone.

For a guided pairing demo, use an operator-only deployment or start an alternate
unpaired snapshot with `YAFFO_DEMO_ALLOW_PAIRING=1` that is reachable only through
IAP. Generate the code on A, paste it into B, then grant the album. Restore the
normal pre-paired snapshot afterwards. The public self-service instance should
use an annotated pairing walkthrough or video and remain paired.

### When per-visitor instance pairs are justified

An isolated sandbox for every visitor would need a control plane that creates a
pair, gives it a random hostname or signed session, applies CPU and transfer
quotas, and destroys it after roughly 30 minutes. That eliminates cross-visitor
interference and makes live pairing safe, but it is a separate product:

- instance lifecycle and routing service;
- per-session identity and secrets;
- concurrency limits and a waiting room;
- reliable TTL cleanup;
- abuse detection and emergency shutdown;
- cost proportional to concurrent pairs.

Do not build this until the shared anonymous demo has shown enough use and
cross-visitor interference to justify it.

## Abusable actions and proposed mitigations

The table below is the initial discussion list, based on the current routes and
background actions. The policy is intentionally conservative where an action can
escape the seeded library, create unbounded work, spend money, or break the
pre-paired sharing story.

| Action surface | Current examples | How it can be abused | Proposed public-demo policy | Mitigation or later option |
|---|---|---|---|---|
| Arbitrary filesystem reads and discovery | `/api/fs/list`, `/media-by-path` | Enumerate the container or host mounts; retrieve keys, config, databases, logs, or `/proc` data | **Blocked** | Remove path-addressed media from demo navigation; serve only DB-backed media IDs; mount only instance data; add path and symlink containment as defense in depth |
| Arbitrary directories and storage reconfiguration | `/api/fs/create-folder`, media-directory add/remove, thumbnail-directory changes, sharing download-directory changes | Create directories, scan unexpected trees, move thumbnails, fill disk, or point the app at sensitive paths | **Blocked** | Bake immutable media/thumbnail/download settings into the golden DB; allow changes only in an operator-only instance |
| Host process launch | `/api/open-file`, `/api/open-folder` | Spawn `open`, `xdg-open`, or associated applications repeatedly; consume processes or reach host integrations | **Blocked** | Disable routes and hide controls; do not install desktop helpers in the runtime image; enforce PID limits |
| File deletion, trash, and movement | Duplicate-removal execution; automation actions that trash or move files | Destroy fixtures, write outside intended destinations, fill trash/storage, or race reset | **Blocked** | Mount seed media read-only; omit trash helpers; demonstrate with screenshots or an operator-only disposable copy |
| LLM credentials and paid generation | LLM API-key set/clear, model selection, page/theme/automation chat | Store a visitor's secret on a shared server, consume an operator key, create cost, generate stored active content, or exhaust workers | **Blocked** | Ship no provider keys; block key and generation routes and background tasks; show pre-generated pages/themes read-only |
| P2P identity, pairing, trust, and grants | Pairing-code generation, pair, revoke/delete/rename device, grant reconciliation, album share | Pair an attacker's device, grant it data, break A/B, consume pairing nonces, or abuse hub sessions | **Blocked** | Pre-pair A/B and seed grants; expose read-only trust/grant views; use the IAP-only pairing mode for a guided demo |
| Heavy indexing and classification | Index sync/reindex, per-media reindex, label reclassification, duplicate finding, face analysis | Sustain CPU/RAM load, grow queues, repeatedly load native models, and make browsing unavailable | **Blocked initially** | Permit only the read-only inventory scan over immutable configured demo directories; use pre-indexed fixtures and require a global concurrency lock, hard timeout, cooldown, and daily quota before allowing any sample mutation job |
| Automations and scheduled work | Create/configure/run/publish automations and triggers; built-in metadata/file actions | Persist repeated work, mutate files and metadata after the visitor leaves, call external services, or fill the queue | **Blocked** | Disable periodic dispatch and all enabled automations in the golden state; show prepared run history read-only |
| External lookup calls | Reverse geocoding and any future URL-backed integration | Turn the demo into a request amplifier, hit provider quotas, or create third-party cost | **Blocked initially** | Use pre-resolved fixture locations; later add a cache-only lookup or strict global/provider quota |
| Full media, preview, and video delivery | `/media/<id>`, posters, remote P2P previews, HTTP Range requests | Scrape or hotlink the fixture set, issue pathological ranges, repeatedly resize previews, and drive network egress | **Limited** | Use small low-resolution licensed fixtures; pre-generate previews; validate ranges; CDN-cache safe responses; per-IP and global byte/request budgets; daily egress kill switch |
| P2P pulls and relay traffic | Remote browse/preview and transfer pull/continue | Repeat batches, fill B's download volume, monopolize transfer slots, or create metered hub relay egress | **Limited to B** | One active batch globally, small file/count/batch-byte caps, per-IP cooldown, download-volume quota, disable “continue anyway” beyond relay budget, clear downloads during the daily reset |
| Seeded metadata edits | Favorites, tags, face/person assignments, location changes | Vandalize shared state, create stored text payloads, trigger event tasks, or confuse other visitors | **Blocked initially** | Let visitors browse prepared examples; reconsider a small scratch-only exception after the read-mostly pilot has usage data |
| Album edits | Create/update/delete albums, add/remove/reorder items, set cover | Row spam, very large selections, deletion of the sharing fixture, and cross-visitor interference | **Blocked initially** | Let visitors browse prepared albums; reconsider one protected-size `Demo Scratchpad` only if editing is important to the walkthrough |
| Global application settings | Locale, distance unit, filter configuration, label vocabulary, theme default | Change the experience for every visitor, trigger work, or leave the UI unusable | **Blocked or browser-local** | Move harmless presentation preferences to a signed cookie or local storage for demo mode; keep database-backed global settings immutable |
| People and other row creation/deletion | Person create/update/delete and similar catalog mutations | Unbounded DB growth, offensive stored names, deletion of fixtures, or expensive embedding recalculation | **Blocked initially** | Consider bounded rename/assignment on designated scratch records only; enforce length/character rules and protected seed IDs |
| Job and transfer administration | Cancel/delete jobs; cancel/continue/delete transfer records | Interrupt another visitor, hide evidence of abuse, or bypass a transfer budget | **Blocked** | No public job creation means no public job controls; let the capped B transfer finish and clear it during reset |
| Query and search amplification | Large filter lists, repeated facets/autocomplete, custom-page widget queries, extreme pagination | Expensive SQLite queries, large responses, cache busting, and worker/thread starvation | **Limited** | Bound list lengths, page size, text length, and query complexity; set request/DB timeouts; cache stable facets; cap concurrent requests per IP and globally |
| Stored HTML/CSS/widget content | Custom pages, widget previews/state, generated themes and SVG/CSS assets | Stored XSS, phishing content, CSP bypass attempts, or persistent visual defacement | **Blocked for authoring** | Serve only reviewed, pre-generated content; preserve widget sandboxing; apply a tested CSP and output escaping |
| Service and host information | Settings/system-info pages, error detail, logs, metrics, health/admin endpoints | Reveal versions, absolute paths, topology, device IDs, workload state, or operational controls | **Blocked or sanitized** | Provide a demo-specific About page; keep detailed health/metrics on localhost or IAP; return generic public errors |
| Request forgery and cross-site triggering | Every allowed state-changing form or API | Another site can cause visitors' browsers to spend demo resources or mutate shared state even though no login is present | **Blocked without CSRF token** | Apply CSRF to all mutations, validate `Origin`/`Sec-Fetch-Site`, use secure same-site cookies, and reject unsupported content types |

### Policy states and enforcement

- **Allowed** means ordinary browsing of seeded, non-sensitive data. Responses can
  be cached where correct, but still receive input-size and concurrency limits.
- **Limited** means the feature is useful to the demo and has an application-level
  per-IP/session budget plus a global backstop. The anonymous session cookie is a
  convenience for fair-use accounting, not proof of identity; clients can clear
  it, so global caps remain mandatory.
- **Blocked** means Flask returns a consistent `demo_feature_disabled` response
  before parsing a body or starting work. Templates remove or annotate the
  control, but the server check is authoritative.
- **Operator-only** means the capability is absent from public routing and is
  available only through IAP or a separately started maintenance profile.

The current route inventory contains 144 rules: 57 `GET`, 85 `POST`, one `PUT`,
and two `DELETE`. No rule combines `GET` with an unsafe method. Normal gallery,
detail, people, album, remote-share, preview, and filter browsing use `GET`.
There are two browse-adjacent exceptions:

- reviewed custom pages initially render through `GET`, but an interactive widget
  can use `POST` for a live, read-only data query and a separate `POST` to persist
  state;
- Device B browses A's grants, files, previews, and transfer status through `GET`,
  but starting an actual pull is `POST`.

Use one Flask `before_request` gate with the convention that GETs are allowed and other methods are disallowed. 
When demo mode is active, reject every unsafe method before parsing its body
unless it has an attribute to allow it. Certain get methods `yaffo/routes/base.py:fs_list` for example. Should
have an attribute to disallow. The widget-query exception is read-only, has
validated query shapes, bounded result sizes and rate limits, and never includes
widget-state persistence.

#### Consistent blocked-action feedback

The request gate must return HTTP `403` for blocked API, `fetch`, and HTMX
requests without invoking the route:

```json
{
  "error": "This action is disabled in the public demo.",
  "code": "demo_feature_disabled"
}
```

Return `Content-Type: application/json` and `Cache-Control: no-store`. Keep the
message translatable and stable; clients detect the `code`, not the English text.
For an ordinary browser navigation or form submission that does not request an
API response, render a small demo-disabled HTML response with the same message
instead of exposing JSON as a page.

Load one global demo-response module from `base.html` after `notification.js`.
It should:

1. observe every `fetch` response by cloning only `403` JSON responses, and
   dispatch `yaffo:demo-feature-disabled` when the code matches;
2. inspect HTMX error responses, suppress the failed fragment swap when the code
   matches, and dispatch the same event; and
3. have one event listener call
   `window.notification.info(message, 5000)`.

This keeps demo-specific handling out of the individual gallery, settings,
sharing, and builder modules. The original `Response` must still be returned to
the caller so existing feature error handling does not break. Add deduplication
so one rejected response produces one toast. The toast is explanatory UI only;
the Flask gate remains authoritative, and unavailable controls should still be
hidden or annotated where practical.

### Proposed v1 interactive set

Keep the first release useful while defaulting to read-only:

- allow all normal browsing, filtering, pagination, detail views, people/faces,
  labels, locations, albums, and reviewed custom-page presentations;
- allow validated live queries from reviewed custom-page widgets, but do not
  persist widget state;
- allow B to browse A's seeded grants, preview remote items, and start one capped
  transfer batch; let it finish without public cancel/resume/delete controls;
- allow all utility GET routes so visitors can view Index Photos, Remove
  Duplicates, automation details, run history, and editor screens; block every
  utility POST, including indexing, duplicate actions, automation execution,
  configuration, and authoring;
- block other filesystem/configuration APIs, AI and custom-content authoring,
  global settings, external lookups, P2P trust/grant mutations, metadata and
  album edits, transfer administration, and seed-record deletion.

This split is the v1 baseline. Expand the central exception set only when a
specific walkthrough step justifies the additional abuse controls and tests.

## Security requirements

Anonymous visitors control the deliberately small capability set above. Treat
every allowed request as hostile input and assume per-client limits can be evaded
with distributed traffic; container and global cost backstops are still required.

### Required before any remote access

1. **Use demo-only data.** Include no personal library, real face embeddings,
   precise GPS records, API keys, production databases, SSH keys, cloud
   credentials, or identifiable EXIF metadata. Record the source and license of
   every fixture.
2. **Add an application-level demo mode.** Deny risky routes on the server, not
   only by hiding navigation. Use the central unsafe-method gate, exact exception
   set, and public-read allowlist described above. At minimum, block the filesystem
   list/create APIs, `open-file`, `open-folder`, media-directory and
   thumbnail-directory changes, arbitrary scans, duplicate-file actions, LLM key
   management and generation, device revoke/delete/rename, and raw path-based
   media access. Allow only the widget-query and bounded receiver-pull exceptions
   the walkthrough needs.
3. **Contain every filesystem path.** Resolve it and require it to be under the
   instance's explicit media, thumbnail, download, or temp roots before reading,
   writing, scanning, or deleting. Add negative traversal and symlink tests. The
   [OWASP traversal testing guidance](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/05-Authorization_Testing/01-Testing_Directory_Traversal_File_Include)
   is a useful test inventory.
4. **Add CSRF protection to all state-changing web requests.** `SameSite=Lax`
   helps but is not a substitute. Flask's own [security guidance](https://flask.palletsprojects.com/en/stable/web-security/)
   recommends a token for requests that modify server content.
5. **Set production Flask values.** Use random, distinct secrets; set secure and
   HTTP-only cookies; restrict `TRUSTED_HOSTS`; apply `ProxyFix` only for the exact
   trusted proxy hop; limit request size; and return HSTS,
   `X-Content-Type-Options`, a tested Content Security Policy, frame restrictions,
   and a conservative referrer policy.
6. **Use a production WSGI server with debug disabled.** Waitress already meets
   the server requirement. Flask explicitly says its development server is not
   designed to be secure or stable for production; see [Deploying to
   Production](https://flask.palletsprojects.com/en/stable/deploying/).
7. **Run containers as non-root.** Use a read-only root filesystem, a size-limited
   tmpfs, `no-new-privileges`, dropped Linux capabilities, PID/memory/CPU limits,
   no Docker socket, and only the instance-specific writable mounts.
8. **Constrain work and spend.** Start no task host, workers, watcher, or periodic
   dispatcher. Reject job-producing routes before enqueueing, clear any stale
   queue rows during reset, cap P2P transfer bytes, rate-limit expensive routes
   per anonymous session and IP, add global concurrency/byte backstops, and omit
   every paid-model API key.
9. **Protect the VM.** Use a dedicated least-privilege service account, IAP/OS
   Login for administration, no public SSH, automatic security updates, and a
   Shielded VM with Secure Boot after compatibility testing. Shielded VM provides
   vTPM, measured boot, and integrity monitoring at [no separate
   charge](https://cloud.google.com/security/products/shielded-vm).
10. **Make reset reliable.** Stop or drain both apps before replacing SQLite and
    queue files. Never copy a live SQLite directory. Preserve the stable identity
    secrets, restore the paired database snapshots and seed media, clear downloads
    and logs, restart, and run a P2P ping/list/pull smoke test.

### Network policy

- Use a dedicated VPC with no permissive default ingress rules. Do not inherit
  the default VPC's broad SSH rule. Allow public inbound TCP only on 80 and 443
  to the tagged demo VM. If SSH is retained, allow TCP 22 only from IAP's
  published TCP-forwarding range. Do not expose app, database, metrics, reset,
  Docker, or P2P container ports directly.
- Attach one reserved static external IPv4 address to the VM for DNS, inbound
  HTTPS, and outbound connectivity. The design needs neither Cloud NAT nor a GCP
  load balancer.
- Prefetch model and binary assets. After deployment, restrict outbound traffic as
  far as practical to `hub.yaffo.app`, DNS/NTP, certificate authorities, Google
  APIs needed by the service account, and update endpoints.
- Keep the app instances on a private container network. Exposing their P2P UDP
  ports on the VM is unnecessary for relay-first correctness; use a separate test
  topology when validating inbound direct paths.

### Operations and incident response

- Health-check the public walkthrough and both app home pages, plus private
  detailed health, P2P hub connectivity, and a small A-to-B listing request.
- Alert on container restart loops, disk fullness, elevated application rate-limit
  rejections, queue depth, long-running jobs, relay byte growth, network egress,
  and spend.
- Set budget alerts at 50%, 80%, and 100%, but remember that [Google Cloud budgets
  do not cap spending automatically](https://docs.cloud.google.com/billing/docs/how-to/budgets).
  Add an operator-tested emergency stop that disables the public ingress firewall
  rule and stops the VM.
- Retain only the logs required for troubleshooting. Avoid logging pairing codes,
  cookies, secrets, full filesystem paths, or media metadata.
- Patch monthly and rebuild the image from pinned dependencies. Scan the image in
  CI or on demand rather than enabling an unexpected paid scanning service.
- Document how to revoke the paired demo devices in their local trust stores and
  rotate the file-backed identities, Flask secrets, TLS state, and operator
  sessions if the VM or image is compromised. The hub has no identity registry
  or denylist to update.

## Cost estimate

The following is a planning estimate in USD for `us-central1`, using public list
prices reviewed on 2026-07-18. Taxes, negotiated discounts, build minutes,
logging, vulnerability scanning, and traffic outside North America are excluded.
Re-run the [Google Cloud Pricing Calculator](https://cloud.google.com/products/calculator)
before provisioning.

| Item | Assumption | Always on | About 180 hours/month |
|---|---:|---:|---:|
| Compute | One `e2-medium`, about $0.0335/hour | ~$24 | ~$6 |
| Persistent Disk | 50 GiB `pd-balanced`, about $0.10/GiB-month | ~$5 | ~$5 |
| External IPv4 | Static address associated with the VM, $0.005/hour | ~$3.65 | ~$3.65 |
| Artifact Registry | About 5 GiB retained; first 0.5 GiB free, then ~$0.10/GiB-month | ~$0.45 | ~$0.45 |
| Internet transfer | Example 25 GiB to North America; first 1 GiB free, then $0.12/GiB | ~$2.88 | ~$2.88 |
| Existing domain | Reuse `yaffo.app` | $0 incremental | $0 incremental |
| **Estimated demo total** | Round for small logging/build variation | **~$35–45/month** | **~$18–25/month** |

Price references:

- GCP lists `e2-medium` at about $0.0335/hour; see [General-purpose VM
  pricing](https://cloud.google.com/products/compute/pricing/general-purpose).
- GCP lists balanced disk at about $0.10/GiB-month in Iowa; see [Disk and image
  pricing](https://cloud.google.com/compute/disks-image-pricing?hl=en).
- An in-use VM external IPv4 address is [$0.005/hour](https://cloud.google.com/vpc/network-pricing#ipaddress).
  Google considers a static address associated with a VM to be in use even while
  that VM is stopped, so both estimates include a full month.
- Premium-tier outbound data to North America is [free for the first GiB and
  $0.12/GiB through 1 TiB](https://cloud.google.com/vpc/network-pricing).
- Artifact Registry storage is [free for 0.5 GiB, then about
  $0.10/GiB-month](https://cloud.google.com/artifact-registry/pricing).

Anonymous traffic makes egress the least predictable line item. At the listed
North America rate, 25 GiB is about $2.88 after the free GiB, 100 GiB is about
$11.88, and 500 GiB is about $59.88. The headline totals above are therefore a
low-traffic planning case, not a cap. Enforce daily byte thresholds at the app,
alert on GCP and hub egress, and automatically disable public ingress when the
emergency threshold is crossed.

The existing always-on P2P hub is not included because it is shared product
infrastructure rather than a demo-only resource. Its current design estimate is
roughly $0–7/month for the VM plus disk and relayed egress; review actual billing
and `total_bytes_forwarded` before increasing demo traffic.

Do not add a second app VM merely for visual credibility; reserve it for network
behavior that cannot be shown on one host. Likewise, do not include indexing or
native model loading in the capacity test for a deployment where those operations
are disabled.

## Delivery plan

### Phase 0 decision record

Accepted on 2026-07-18. These decisions are the operating contract for the
first pilot. Changing the audience, unsafe-method exceptions, operating hours,
fixture rights, or spending ceiling requires updating this record before the
deployment changes.

#### Audience and capability policy

- The pilot is an anonymously accessible public product demo. It has no visitor
  login, invitation code, or claim of per-visitor isolation. The walkthrough and
  both Yaffo instances may be linked from the public Yaffo site only after the
  Phase 4 acceptance checklist passes.
- The public demo uses the read-mostly v1 interactive set described above. The
  only unsafe-method exceptions to the Phase 1 fail-closed gate are:
  - `POST` to the Flask endpoint `faces_assign` on both `source` and `receiver`,
    limited to 50 currently unassigned faces per request and executed in the web
    process without enqueueing a task;
  - `POST` to the Flask endpoint `pages_version_widget_query` on both `source`
    and `receiver`, limited to reviewed, published widgets and read-only bounded
    queries;
  - `POST` to the Flask endpoint `sharing_device_pull_selected` on `receiver`
    only, limited to one bounded transfer batch from the pre-paired source.
- No other `POST`, `PUT`, `PATCH`, or `DELETE` endpoint is public. In particular,
  widget state persistence, transfer administration, pairing, trust, and grant
  changes are not exceptions. The exact public `GET` endpoint allowlist is part
  of Phase 1 and remains fail-closed until it is committed and tested.
- The public deployment is a shared disposable sandbox, not a hosted account or
  cloud storage service. The walkthrough must say that visitors can affect the
  one shared transfer slot and that state is reset automatically.

#### Hours, reset interval, and pilot duration

- Before public launch, the hostnames remain unpublished or public ingress stays
  disabled except during operator testing.
- Once linked publicly, the pilot is available every day from 8:00 AM through
  10:00 PM in the `America/Chicago` time zone. The walkthrough must publish these
  hours and show an offline explanation outside the service window.
- The VM starts at 7:45 AM `America/Chicago`. Startup restores both instances
  from their golden state before Caddy or either app is considered ready. A
  failed restore keeps the public services unavailable. The VM stops at 10:00 PM.
- The startup restore is the single scheduled daily reset. Deployments and
  emergency response may reset sooner. A reset drains and stops both apps before
  replacing SQLite data, as required by the reset design above.
- The initial evaluation window is six weeks from public launch. At its end the
  operator reviews usage, interference, reliability, egress, and cost against the
  decision points below before extending the pilot.

#### Fixture ownership and license policy

- The public demo does not reuse `yaffo_ui_tests/test_data`, personal libraries,
  stock-photo downloads, or media copied from the internet. Existing test media
  is not approved for public redistribution and includes identifiable people.
- Phase 3 creates a purpose-built synthetic library for this demo. Synthetic
  people are fictional and must not intentionally resemble a real person. Short
  video fixtures are rendered from the synthetic source artwork so the demo does
  not depend on a separately licensed video library.
- The Yaffo project operator owns or has the necessary rights to the generated
  fixture outputs and approves each one for redistribution under `CC0-1.0`
  before it enters a golden fixture. No third-party attribution requirement is
  accepted for the pilot fixture set. Generated assets are deployment data, not
  automatically covered by the repository's MIT license.
- Device A (`Family Mac`) contains 18 still images and two short, low-resolution
  videos. Its `Trips/Chicago` folder and `Chicago Weekend` album span at least two
  child folders, and at least three sibling items remain outside all grants.
  Device B (`Travel Laptop`) contains eight different still images and one short
  video; its download directory starts empty.
- Fixture metadata is fabricated for the demo. It contains no imported EXIF
  identity, serial number, account, or precise real-person location history.
  Approximate Chicago landmarks may be used for the location UI. Names assigned
  to synthetic people must be clearly fictional.
- Every file admitted to a golden fixture requires a manifest entry containing
  its stable fixture id, SHA-256 digest, owning device, media type, synthetic
  generation or rendering method, creation date, rights owner, the `CC0-1.0`
  license identifier and approval, intended folder/album/grant membership, and a
  metadata-scrub review. Phase 3 must fail fixture preparation when the manifest
  and files differ.

#### Budget and operational ownership

- The incremental pilot budget is **USD 50 per calendar month**, including
  compute, disk, static IP, registry storage, demo egress, and demo-attributable
  hub relay egress. The operating target is at most USD 45 per month so the
  ceiling retains a small response margin.
- Starting the VM 15 minutes before the 14-hour service window is about 430
  running hours in a 30-day month. At the planning prices above, compute is
  approximately USD 15 per month and the total low-traffic pilot remains within
  the USD 50 ceiling.
- Billing budget notifications must be configured at 50%, 80%, and 100% of the
  USD 50 ceiling. Monitoring must also alert on forecast overspend and abnormal
  daily egress. Reaching or forecasting the ceiling triggers withdrawal of
  public ingress; budgets alone are not treated as a spending cap.
- **Jason Turan** is the pilot owner and initial alert recipient. Jason may run
  an early reset, withdraw the public DNS/proxy path, disable the public ingress
  firewall rule, or stop the VM. Deployment IAM must grant these controls to the
  operator identity only; no reset or emergency endpoint is public.
- Jason owns fixture-rights approval, billing review, incident response, and the
  go/no-go launch decision. Before Phase 4 launch approval, a second operator
  must be named and must successfully follow the reset and emergency-stop
  runbook; until then the demo is not considered production-ready.

Phase 0 is complete: the audience, hours, data ownership, exception policy,
monthly budget, and current operational owner are recorded above.

### Phase 0 — decide the operating policy

- [x] Confirm anonymous public access and the v1 exception set: reviewed widget
  queries on A/B and one bounded transfer start on B.
- [x] Confirm scheduled hours versus 24/7: use 8:00 AM–10:00 PM
  `America/Chicago` every day, with VM startup at 7:45 AM for the daily reset.
- [x] Choose and document demo fixtures and licenses.
- [x] Decide who can reset the sandbox, withdraw public routing, stop it, and
  receive alerts.

Exit criterion: audience, hours, data ownership, and monthly budget are written
down.

### Phase 1 — harden the application boundary

- Implement `YAFFO_DEMO_MODE`, `YAFFO_DEMO_ROLE`, the centralized unsafe-method
  gate, its exact exception set, and the public-read allowlist. Add route-map tests
  that validate the named endpoints and prove everything else fails closed.
- Add the global `demo_feature_disabled` response handler for `fetch` and HTMX,
  the shared informational toast, and the non-JavaScript HTML fallback.
- Add service-layer demo guards around filesystem changes, task enqueueing, LLM
  calls, and P2P trust/grant mutation.
- Add path-root enforcement and symlink/traversal tests.
- Add application-wide CSRF protection and production cookie/host/proxy settings.
- Make the web bind host configurable.
- Add per-session, per-IP, and global request/query/transfer/byte limits, plus
  protected seed records and the receiver download-volume limit.
- Ensure the UI clearly marks the environment as disposable and shows the next
  reset time.
- Allow face assignment POST methods but execute the shared assignment logic in
  the web process instead of enqueueing a background task in demo mode.

Phase 1 implementation progress as of 2026-07-18:

- Complete: startup-only mode/role configuration, exact public-read policy,
  role-scoped unsafe-method exceptions, fail-closed route-map validation, stable
  JSON/HTML blocked responses, and fetch/HTMX feedback.
- Complete: disposable/reset banner, global CSRF protection, secure demo cookie
  and response headers, trusted-host/proxy configuration inputs, configurable
  Waitress bind host/thread count, and a demo startup path without task workers,
  watcher, periodic dispatcher, or runtime asset downloads.
- Complete: service-level task, filesystem-browser, paid-key, pairing, trust,
  and grant guards; DB-backed media root containment; traversal/symlink tests;
  synchronous bounded assignment of unassigned faces; request/query cooldowns;
  and transfer count, byte, active-batch, and receiver-volume caps.
- Remaining: audit and contain every non-media filesystem consumer, protect seed
  records outside face assignment, add byte accounting for media/preview delivery,
  annotate or hide each unavailable control, and complete the security review
  against the Phase 1 exit criterion.

Exit criterion: a security review finds no route that can escape an instance's
mounts, manage paid secrets, start unbounded work, or alter P2P identity/trust
outside the scripted demo.

### Phase 2 — build the two-instance deployment

- Create the runtime image and a local Compose definition first.
- Run A and B with separate mounts, ports, secrets, and request-concurrency limits.
- Add the static walkthrough files, mount them read-only into Caddy, and configure
  Caddy to serve `demo.yaffo.app` directly while proxying only the A/B hostnames.
- Extend Terraform with a dedicated VPC, VM egress, least-privilege service
  account, Shielded VM options, static external IP, disk, start/stop schedule,
  firewall, budget, and DNS outputs.
- Pin image digests in deployment and retain only a small number of versions.

Exit criterion: a clean `terraform apply` plus deploy command produces three
anonymous public hostnames, private operator controls, and no manually edited VM.

### Phase 3 — fixtures, pairing, and reset

- Import/index both fixture libraries during image or golden-state preparation,
  not at every boot.
- Pre-pair stable identities and create the intended grants.
- Save immutable golden data directories after the queue is drained and SQLite is
  closed.
- Implement an idempotent reset once per day during scheduled startup and
  validate it after interruption halfway through.

Exit criterion: reset removes changes/downloads, restores the same paired device
IDs and grants, and completes a small A-to-B pull.

### Phase 4 — verification and launch

- Run unit/integration tests plus the existing sharing UI specs.
- Run an anonymous remote-browser smoke test for every walkthrough step.
- Test CSRF failures, route blocks, traversal and symlink attempts, per-client and
  global rate limits, resource exhaustion, direct app-port reachability, and
  public-ingress shutdown.
- Load-test expected anonymous concurrency plus a controlled abuse burst.
- Exercise backup/restore, key rotation, emergency stop, and budget notification.
- Run one real two-network test against the production hub so the demo does not
  mask a sharing regression.

Exit criterion: the acceptance checklist below passes and an operator other than
the implementer can run and reset the demo from the runbook.

## Acceptance checklist

- All demo hostnames are anonymously reachable over HTTPS through Caddy; app
  container ports and operator endpoints are not directly reachable.
- `demo.yaffo.app` is served directly from reviewed, read-only static files by
  Caddy and has no application process, database, cookie, API, or separate port.
- No real photos, face data, location history, or paid API credentials exist on
  the VM or in its image.
- A and B have different device IDs, database files, secrets, mounts, and ports.
- A and B reconnect to the hub after restart and remain mutually paired.
- B sees only A's granted folder/album, cannot list a non-granted sibling, and can
  pull a selected fixture.
- Revocation prevents future access but does not claim to delete already pulled
  files; this remains covered by the sharing integration test even though public
  trust controls are disabled.
- Blocked routes fail server-side even when requested directly.
- Blocked `fetch` and HTMX actions show one informational toast without replacing
  the current page or fragment; direct non-JavaScript requests show the fallback
  demo-disabled page.
- State-changing requests without a valid CSRF token fail.
- Every unsafe method is rejected unless its exact endpoint and role are in the
  central exception set; public read routes are explicitly allowlisted, and CI
  proves new routes fail closed.
- Protected seed records cannot be changed; widget queries and the receiver pull
  obey query, count, byte, destination, concurrency, and rate limits.
- Neither container can read the other's data or the host filesystem.
- No task host, worker, watcher, or periodic dispatcher process is running;
  per-instance memory, CPU, requests, and transfer bytes are capped.
- Reset is automatic, observable, and proven to restore the golden state.
- Alerts, budget thresholds, emergency stop, patching, and key rotation have named
  owners and a tested runbook.

## Decision points after the pilot

After four to six weeks, use privacy-conscious aggregate request metrics,
walkthrough completion, policy blocks, reset failures, resource peaks, egress,
and cost data to choose among:

1. Keep the anonymous two-instance sandbox and adjust the allowed feature set.
2. Replace it with a cheaper read-only static tour if visitors rarely use the live
   controls.
3. Add guided unpaired snapshots if live pairing is repeatedly valuable.
4. Build per-visitor ephemeral pairs only if simultaneous public demand justifies
   the control-plane and abuse-prevention work.

Do not treat the pilot as evidence that Yaffo itself should gain accounts or
become multi-tenant. Demo mode constrains one disposable deployment; Yaffo's
local-first and device-key sharing model remains unchanged.
