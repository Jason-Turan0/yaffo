# Demo Environment

Reference for Yaffo's public demo deployment, as built under `deploy/demo/`. That
directory's `README.md` is the operator runbook (build, deploy, seed, reset, SSH,
emergency stop); this document explains what the environment is, how its safety model
works, and why it is shaped the way it is.

Last reviewed: 2026-07-19

## Overview

The demo is an **anonymous, resettable sandbox** of two pre-paired Yaffo instances
running on a single Compute Engine VM:

- `demo-a.yaffo.app` — the sharing device (`Bennett Family`), seeded with a
  synthetic library and prepared albums/grants.
- `demo-b.yaffo.app` — the receiving device (`Obama Family`), seeded with a
  different library and an empty download directory.
- `demo.yaffo.app` — a static HTML walkthrough, served directly by Caddy, with
  links that open A and B in separate tabs.
- Caddy on the demo VM provides automatic HTTPS, serves the walkthrough files
  directly, and reverse-proxies the A/B hostnames to their private containers.
- The existing `hub.yaffo.app` provides P2P signaling, STUN, and encrypted relay
  fallback.
- `YAFFO_DEMO_MODE=1` on both app instances enables a centralized, fail-closed
  HTTP-method gate, immutable demo configuration, workload and transfer caps, and a
  disposable-demo banner.
- A golden-state restore runs on every VM boot (including the daily scheduled
  start) and on operator demand, plus an emergency stop.

This demonstrates real Yaffo behavior — the actual device-key identity and P2P
browsing path — while keeping the hosting and operational model small. It is not a
multi-user or production cloud edition of Yaffo.

The demo is deliberately reachable without a login. Its safety boundary is not
visitor identity; it is the deliberately restricted capability set exposed by demo
mode, backed by container isolation, rate and resource limits, disposable data, and
automatic reset. The unrestricted local application is never reachable from the
public hostnames.

The separate `deploy/yaffo_peer` topology remains the tool for real-network and
relay-fallback validation; it is intentionally kept apart from this stable product
demo.

## What a visitor can do

1. Browse a realistic but non-sensitive photo and video library.
2. Browse filters, people, labels, locations, favorites, albums, and reviewed
   custom pages.
3. See that pairing and sharing grants are different concepts.
4. Open Device B, browse a share from Device A, and preview remote media.
5. Start one bounded transfer batch from B against the pre-paired source.

The demo intentionally does **not** provide storage for visitor uploads, multiple
isolated users, an availability/durability SLA, face-recognition or bulk-indexing
benchmarking, proof of NAT traversal between independent networks, or access to the
AI builders and paid model APIs.

## Anonymous access model

Yaffo sharing has no user accounts by design: a P2P device authenticates to the hub
and to another device with its ECDSA keypair, and a human establishes trust with a
one-time pairing code. The demo preserves that accountless product story rather than
adding a visitor login.

The normal web app assumes that anyone who can reach its listening socket is the
device owner, so the public deployment runs under a separate runtime contract, demo
mode:

- `YAFFO_DEMO_MODE=1` is read once at startup and cannot be changed through the UI
  or a request.
- `YAFFO_DEMO_ROLE=source|receiver` narrows policy per instance: A is the
  mostly-read-only sharing source; B is allowed one bounded transfer control.
- A single application-wide `before_request` gate rejects `POST`, `PUT`, `PATCH`,
  and `DELETE` unless the exact method, Flask endpoint name, and demo role appear in
  a small central exception set. A new mutation is therefore blocked without needing
  a decorator on its route.
- Public `GET`/`HEAD`/`OPTIONS` endpoints come from a central allowlist that
  excludes read-like routes exposing the filesystem, configuration, or expensive
  operations (for example the filesystem-list route). HTTP method alone is not
  treated as a sufficient safety classification.
- Blocked endpoints fail inside the Flask application, not merely in templates or at
  the proxy. Dangerous service functions also reject calls in demo mode when reached
  by a non-route path.
- The UI keeps disabled controls visible where they help explain the product, but a
  shared handler explains rejected actions rather than pretending the capability is
  absent.

This is a deployment safety profile for one disposable public instance, not an
authorization system, and it does not extend into the desktop product.

### The three unsafe-method exceptions

Every other `POST`/`PUT`/`PATCH`/`DELETE` is blocked. The only exceptions to the
fail-closed gate are:

- `faces_assign` (both roles) — limited to 50 currently-unassigned faces per
  request, executed synchronously in the web process instead of enqueueing a task.
- `pages_version_widget_query` (both roles) — reviewed, published widgets only, with
  read-only bounded queries; widget-state persistence is **not** included.
- `sharing_device_pull_selected` (receiver only) — one bounded transfer batch from
  the pre-paired source, allowed to finish without public cancel/resume/delete
  controls.

Pairing, trust, grant changes, transfer administration, configuration, authoring,
and metadata/album edits are not exceptions.

### Blocked-action feedback

The gate returns HTTP `403` for blocked API/`fetch`/HTMX requests without invoking
the route:

```json
{
  "error": "This action is disabled in the public demo.",
  "code": "demo_feature_disabled"
}
```

Responses carry `Content-Type: application/json` and `Cache-Control: no-store`;
clients detect the stable `code`, not the English text. An ordinary browser
navigation or form submission that does not request an API response gets a small
demo-disabled HTML page instead of raw JSON.

A single global demo-response module (loaded from `base.html` after
`notification.js`) observes `fetch` and HTMX responses, suppresses the failed
fragment swap when the code matches, and shows one deduplicated informational toast.
The original `Response` is still returned so existing feature error handling keeps
working. The Flask gate remains authoritative; the toast is explanatory UI only.

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
      +-- demo-a.yaffo.app ------> Yaffo A web :5101  (role: source)
      |                            task system: off
      |                            /data/a only
      |
      +-- demo-b.yaffo.app ------> Yaffo B web :5102  (role: receiver)
                                   task system: off
                                   /data/b only

Yaffo A P2P UDP :5201 ----\
                            +-- WSS + QUIC/UDP --> hub.yaffo.app
Yaffo B P2P UDP :5202 ----/                signaling/STUN/relay
```

Caddy uses exact site blocks (no catch-all), mounts the reviewed walkthrough
directory read-only at `/srv/walkthrough`, serves the walkthrough HTML with
`Cache-Control: no-cache`, and adds a same-origin CSP with `frame-ancestors 'none'`
plus HSTS, `X-Content-Type-Options`, frame, and referrer headers. The app ports stay
on the private container network and are never bound to the VM's public interface.

## Deployment

### Runtime image

The multi-stage root `Dockerfile` builds Python wheels, installs only runtime
libraries, and bakes ExifTool, ffmpeg, InsightFace, and CLIP into `/opt/yaffo-assets`
at build time. The demo entrypoint starts only Waitress and the P2P engine — no
task-queue host, task workers, filesystem watcher, periodic dispatcher, or runtime
asset downloads. `YAFFO_WEB_HOST`/`YAFFO_WEB_PORT`/`YAFFO_WEB_THREADS` make the bind
interface, port, and thread count configurable (four threads per app); the app never
binds directly to a public interface. Each P2P identity is a per-instance file-backed
key with atomic writes and `0600` permissions rather than a desktop keychain, kept on
a volume separate from the resettable data directory.

### Containers

`compose.local.yml` (local smoke) and `compose.prod.yml` (VM) run `demo-a` (source)
and `demo-b` (receiver) with separate data, fixture, identity, and Flask-secret
mounts. Each app container runs as UID/GID `10001`, read-only root filesystem,
`cap_drop: ALL`, `no-new-privileges`, `pids_limit: 128`, `mem_limit: 1200m`,
`cpus: 0.45`, and a size-limited `tmpfs`, publishing no host ports and never
receiving the Docker socket. Fixture media is mounted read-only. Each container sees
only its own data and media — no shared `/data` tree — so sharing happens over
Yaffo's P2P protocol, not a host directory visible to both.

Caddy is the only published service (`mem_limit: 192m`, `cpus: 0.10`, ports 80/443,
`NET_BIND_SERVICE` only) and depends on both apps being healthy. Production requires
digest-pinned Yaffo and Caddy images.

### GCP infrastructure (Terraform)

The `.tf` files provision the VM, modeled on the production `deploy/hub` pattern:

| Resource | Name | Purpose |
|---|---|---|
| Static external IP | `yaffo-demo-ip` | All three hostnames point here; survives VM replacement |
| Firewall rules | `yaffo-demo-ingress`, `yaffo-demo-iap-ssh`, `yaffo-demo-deny-ssh` | 80+443/tcp (Caddy); SSH via IAP only, explicitly denied otherwise |
| VM | `yaffo-demo` | `e2-medium`, Shielded, Container-Optimized OS; startup script brings the stack up |
| Persistent disk | `yaffo-demo-data` | 50 GiB `pd-balanced`; isolated A/B data, identity, and fixture trees |
| Artifact Registry | `yaffo-demo` | Digest-addressed images, bounded retention |
| Service account | `yaffo-demo-runtime` | Least-privilege: pull images, write logs/metrics |
| Schedule | `yaffo-demo-daily-schedule` | Daily start 7:45 AM / stop 10:00 PM `America/Chicago` |
| Budget | "Yaffo public demo" | 50/80/100% + forecast email alerts on a $50/month ceiling |

Two deliberate divergences from the original design, both to match the proven hub
deployment rather than reinvent one:

- **Default VPC with a tag-scoped SSH deny**, not a dedicated VPC. Public inbound is
  limited to TCP 80/443 on the tagged VM; a tag-scoped deny overrides the default
  VPC's broad `default-allow-ssh`.
- **A static admin SSH key installed by the startup script**, not OS Login (which
  proved unreliable for the hub). Port 22 is closed to the internet; the only way in
  is an IAP tunnel with the dedicated, gitignored, passphrase-less automation key.

Only Caddy's 80/443 are exposed; app, database, metrics, reset, Docker, and P2P
ports are never public. Model and binary assets are prefetched into the image, so
outbound needs are limited to the hub, DNS/NTP, certificate authorities, and the
Google APIs the service account uses.

### Delivery flow

`build-and-push.sh` builds `linux/amd64`, pushes a unique tag to the Terraform-managed
registry, and prints the immutable digest. `deploy.sh` copies the
Compose/Caddy/walkthrough bundle to `/var/lib/yaffo-demo/deploy` over IAP,
authenticates Docker with the VM's service account, pulls the exact digests, and
brings the stack up — never touching the golden fixture or identity trees. The VM's
startup script re-runs that same bundle on every boot, so re-run `deploy.sh` after
any code, Caddy, or walkthrough change. The three DNS A records
(`demo`, `demo-a`, `demo-b`) point at the reserved address, with no wildcard or
catch-all record.

## Fixtures and seeded state

`seed-local.sh`/`seed-prod.sh` run `seed_demo.py` inside the built image (with demo
mode overridden for the one-off seeding run) against the same volumes the long-running
containers use, so indexed paths and face/label embeddings match what the running
container serves.

- **Device A (`Bennett Family`, source)** — the purpose-built synthetic library of
  generated people and scenes.
- **Device B (`Obama Family`, receiver)** — real public-domain National Archives
  (NARA) photography, with a checked-in attribution record under
  `yaffo_ui_tests/test_data/obama/`.

Seeding produces indexed media, seeded people/faces, the `Chicago Weekend` album
(spanning multiple folders, with non-granted sibling files so a scoped grant is
visibly not the whole library), a `Florida Trip` custom page, a kid-photo-filing
custom automation, a shared custom theme (neobrutalist default), and the receiver's
pre-configured `/data/downloads` directory. The source records the receiver as a
trusted peer and grants it the folder + album scope; both devices generate real
file-backed P2P identities.

The device display names and the exact album/folder split are the seeded reality;
earlier illustrative names in design discussion (`Family Mac`, `Travel Laptop`,
`Trips/Chicago`) were placeholders.

## Golden state, reset, and operations

`save-golden.sh`/`save-golden-prod.sh` freeze the seeded state after SQLite is
closed. Restore is an atomic staging-swap of each device's data directory back to the
golden copy — identity keys, on a separate volume, are never touched — followed by a
restart and an A/B smoke test. It is safe to interrupt and re-run: a self-heal check
completes (never reverts) a swap a previous run didn't finish.

Restore runs:

- **on every VM boot**, before Caddy or either app is considered ready, via
  `restore-golden.sh` rendered from `files/restore-golden.sh.tftpl` — including the
  daily 7:45 AM scheduled start; a failed restore keeps the public services
  unavailable; and
- **on demand**, via `reset-local.sh` / `reset-prod.sh` (the latter over the same IAP
  SSH tunnel `deploy.sh` uses).

Before any golden state has been saved, restore is a no-op and a fresh VM starts
empty containers.

Emergency withdrawal is deliberately separate from every public hostname:
`emergency-stop.sh --confirm` disables the public firewall rule first, then stops the
VM. A reviewed `terraform apply` restores the declared ingress rule.

Operational expectations: health-check the walkthrough and both app home pages, plus
private detailed health, hub connectivity, and a small A-to-B listing; alert on
restart loops, disk fullness, elevated rate-limit rejections, relay/egress growth,
and spend; budget notifications fire at 50/80/100% and on forecast overspend, and
reaching or forecasting the ceiling triggers ingress withdrawal (GCP budgets do not
cap spend automatically). Logs avoid pairing codes, cookies, secrets, full paths,
and media metadata.

## Machine sizing

The demo runs no task-queue host, workers, watcher, or dispatcher — indexing, face
analysis, label classification, automations, and AI generation are all blocked in
demo mode and precomputed in the golden fixture. The two always-on Yaffo processes
serve HTTP and SQLite reads plus P2P presence and browsing. A sizing probe measured
about 295 MB RSS per web process after loading all routes; two processes plus Linux,
Caddy, SQLite cache, and media responses fit the recommended **`e2-medium`** (1
sustained vCPU across 2 guest vCPUs, 4 GB). `machine_type` is a Terraform variable;
upgrade to `e2-standard-2` only if the VM swaps, exhausts its shared-core CPU
entitlement, or misses a latency target. Do not size for indexing or model inference
— those actions are absent here.

## Security properties

The deployment maintains these invariants; treat every allowed request as hostile
input, and assume per-client limits can be evaded with distributed traffic, so
container and global backstops still apply.

- **Demo-only data.** No personal library, real face embeddings, precise GPS, API
  keys, production databases, SSH keys, cloud credentials, or identifiable EXIF.
- **Server-enforced boundary.** The central unsafe-method gate, exact exception set,
  and public-read allowlist deny risky routes on the server, not by hiding
  navigation. Filesystem list/create, `open-file`/`open-folder`, media/thumbnail
  directory changes, arbitrary scans, duplicate-file actions, LLM key management and
  generation, device revoke/delete/rename, and raw path-based media access are all
  blocked. Only the widget-query, bounded face-assignment, and receiver-pull
  exceptions are open.
- **Path containment.** Every filesystem path is resolved and required to be under
  the instance's explicit media/thumbnail/download/temp roots before use; negative
  traversal and symlink tests cover this. Public media reads resolve DB-backed paths
  beneath configured roots; the one public recursive inventory scan resolves every
  candidate beneath its root and stops at hard file-count/time limits.
- **CSRF + production Flask settings.** Application-wide CSRF on state-changing
  requests, random distinct secrets, secure HTTP-only cookies, restricted
  `TRUSTED_HOSTS`, `ProxyFix` for the single trusted proxy hop, request-size limits,
  HSTS, `X-Content-Type-Options`, a tested CSP, frame restrictions, and a
  conservative referrer policy.
- **Production WSGI, non-root containers.** Waitress with debug off; read-only root
  filesystem, size-limited tmpfs, `no-new-privileges`, dropped capabilities,
  PID/memory/CPU limits, no Docker socket, only instance-specific writable mounts.
- **Constrained work and spend.** No task host/workers/watcher/dispatcher;
  job-producing routes are rejected before enqueue; P2P transfer bytes are capped;
  expensive routes are rate-limited per session and IP with global concurrency/byte
  backstops; no paid-model API keys ship.
- **Protected seed records.** Seeded P2P identity, trust, and grant rows are
  protected at both route and repository boundaries — liveness updates may refresh a
  peer's last-seen value but cannot replace its key, rename/revoke it, or
  create/revoke grants. Byte budgets charge actual `Content-Length` (only the range
  for range responses; `HEAD` charges nothing) against per-session, per-IP,
  global-minute, and global-day limits.
- **VM protection.** Dedicated least-privilege service account, IAP for
  administration, no public SSH, Shielded VM with Secure Boot.
- **Reliable reset.** Both apps drain and stop before SQLite/queue files are
  replaced; stable identity secrets are preserved; downloads and logs are cleared;
  the golden snapshot is restored and smoke-tested.

Numeric budgets (request and widget-query rates, receiver-transfer and inventory-scan
cooldowns, scan file/time bounds, media byte budgets) can be overridden at startup
with the corresponding `YAFFO_DEMO_*` environment variables; invalid or non-positive
values fail startup.

## Abuse surfaces and policy

The policy is conservative wherever an action could escape the seeded library, create
unbounded work, spend money, or break the pre-paired sharing story. **Blocked** means
Flask returns a consistent `demo_feature_disabled` response before parsing a body or
starting work. **Limited** means the feature is useful to the demo and has an
application-level per-IP/session budget plus a global backstop. **Allowed** means
ordinary browsing of seeded data, still with input-size and concurrency limits.
**Operator-only** means absent from public routing, reachable only via IAP or a
separate maintenance profile.

| Action surface | Examples | Policy | Enforcement |
|---|---|---|---|
| Arbitrary filesystem reads/discovery | `/api/fs/list`, `/media-by-path` | Blocked | DB-backed media IDs only; instance-only mounts; path/symlink containment |
| Directory/storage reconfiguration | folder create, media/thumbnail/download dir changes | Blocked | Immutable settings baked into the golden DB |
| Host process launch | `/api/open-file`, `/api/open-folder` | Blocked | Routes disabled; no desktop helpers in the image; PID limits |
| File deletion/trash/movement | duplicate removal, automation move/trash | Blocked | Seed media mounted read-only; trash helpers omitted |
| LLM credentials / paid generation | key set/clear, model select, page/theme/automation chat | Blocked | No provider keys; key and generation routes/tasks blocked; pre-generated content read-only |
| P2P identity/pairing/trust/grants | code generation, pair, revoke/rename, grant, album share | Blocked | Pre-paired A/B, seeded grants, read-only trust/grant views; IAP-only pairing mode |
| Heavy indexing/classification | reindex, reclassification, duplicate/face analysis | Blocked | Pre-indexed fixtures; only the contained inventory scan is public |
| Automations / scheduled work | create/configure/run/publish automations | Blocked | Periodic dispatch off; prepared run history read-only |
| External lookup calls | reverse geocoding, URL-backed integrations | Blocked | Pre-resolved fixture locations |
| Media/preview/video delivery | `/media/<id>`, posters, remote previews, Range | Limited | Small low-res fixtures; validated ranges; per-IP and global byte/request budgets |
| P2P pulls and relay traffic | remote browse/preview, transfer pull | Limited (B) | One active batch, small file/count/byte caps, per-IP cooldown, download-volume quota, cleared on reset |
| Seeded metadata edits | favorites, tags, face/person, location | Blocked (except bounded `faces_assign`) | Browse prepared examples; bounded scratch assignment only |
| Album edits | create/update/delete, add/remove/reorder, cover | Blocked | Browse prepared albums |
| Global application settings | locale, units, filter config, vocabulary, theme | Blocked or browser-local | DB-backed globals immutable |
| People/other row creation/deletion | person create/update/delete | Blocked | Protected seed IDs |
| Job/transfer administration | cancel/delete jobs and transfers | Blocked | No public job creation; the capped B transfer finishes and clears on reset |
| Query/search amplification | large filter lists, facets/autocomplete, pagination | Limited | Bounded list/page/text sizes; request/DB timeouts; per-IP and global caps |
| Stored HTML/CSS/widget content | custom pages, widget state, generated themes | Blocked for authoring | Reviewed pre-generated content; widget sandboxing; tested CSP |
| Service/host information | system-info, error detail, logs, metrics | Blocked or sanitized | Demo About page; health/metrics on localhost/IAP; generic public errors |
| Request forgery / cross-site | any allowed state-changing request | Blocked without CSRF token | CSRF on all mutations; `Origin`/`Sec-Fetch-Site` validation; same-site cookies |

The current route inventory is 144 rules (57 `GET`, 85 `POST`, one `PUT`, two
`DELETE`); no rule combines `GET` with an unsafe method. Normal gallery, detail,
people, album, remote-share, preview, and filter browsing use `GET`. The two
browse-adjacent exceptions are the read-only widget `POST` query and the receiver's
`POST` transfer pull.

## Demonstrating sharing

The walkthrough explains that pairing was performed once during provisioning and did
not by itself grant access. A visitor:

1. In A, opens Sharing and sees B as paired and online (trust/grant mutation controls
   are shown but disabled).
2. Sees A's active folder and album grants.
3. Opens B in a second tab and selects A under **Shared with me**.
4. Browses and filters the remote album, opens a remote preview, and selects small
   files.
5. Pulls them to B and watches progress and the reported destination path.
6. Reads about cancellation, resumption, and revocation in the walkthrough, since
   public visitors cannot alter the shared transfer or trust state.

This single-host topology does not prove a particular network path — every call
begins relayed and may upgrade, and container/GCE NAT determine what the UI reports.
For a guided relay-fallback session, use the `HARD` profile in `deploy/yaffo_peer`.
Because pairing codes are short-lived and single-use, the shared sandbox stays paired
rather than letting concurrent visitors repeat the pairing ceremony; a guided pairing
demo uses an operator-only unpaired snapshot with `YAFFO_DEMO_ALLOW_PAIRING=1`
reachable only through IAP.

## Operating policy

Recorded decisions for the pilot. Changing the audience, exception set, hours,
fixture rights, or spending ceiling requires updating this section first.

- **Audience.** Anonymous public product demo — no login, invitation code, or claim
  of per-visitor isolation. The read-mostly v1 interactive set is the baseline; the
  only unsafe-method exceptions are the three named above (`faces_assign`,
  `pages_version_widget_query`, `sharing_device_pull_selected`). The walkthrough
  states that visitors share one transfer slot and that state resets automatically.
- **Hours.** Available 8:00 AM–10:00 PM `America/Chicago` daily. The VM starts at
  7:45 AM and restores the golden state before serving; it stops at 10:00 PM. Outside
  the window the walkthrough shows an offline explanation.
- **Fixtures.** Bennett is synthetic; Obama is public-domain NARA photography with a
  checked-in attribution record. Fixture metadata is fabricated — no imported EXIF
  identity, serial numbers, accounts, or precise real-person location history; names
  for synthetic people are clearly fictional.
- **Budget.** Incremental ceiling of **USD 50/month** (compute, disk, static IP,
  registry, demo egress, demo-attributable hub relay), operating target ≤ USD 45.
  Notifications at 50/80/100% and on forecast overspend; the ceiling triggers ingress
  withdrawal.
- **Ownership.** **Jason Turan** is the pilot owner and alert recipient, and can run
  an early reset, withdraw public DNS/proxy routing, disable the ingress firewall, or
  stop the VM. Naming a second operator who can follow the reset/emergency runbook is
  the remaining gate before the pilot is treated as production-ready.

## Cost

Planning estimate in USD for `us-central1` at public list prices (excludes taxes,
discounts, build minutes, logging, scanning, and non-North-America traffic). Starting
the VM 15 minutes before a 14-hour daily window is about 430 running hours/month.

| Item | Assumption | About 180 hours/month |
|---|---|---:|
| Compute | One `e2-medium`, ~$0.0335/hour | ~$6 (14h/day ≈ $15) |
| Persistent disk | 50 GiB `pd-balanced`, ~$0.10/GiB-month | ~$5 |
| External IPv4 | Static address, $0.005/hour (billed even when the VM is stopped) | ~$3.65 |
| Artifact Registry | ~5 GiB retained; first 0.5 GiB free | ~$0.45 |
| Internet transfer | Example 25 GiB to North America; first 1 GiB free, then $0.12/GiB | ~$2.88 |
| **Total** | Low-traffic planning case, not a cap | **~$18–25/month** |

Anonymous egress is the least predictable line item (25 GiB ≈ $2.88, 100 GiB ≈
$11.88, 500 GiB ≈ $59.88 after the free GiB). Daily byte thresholds, egress alerts,
and automatic ingress withdrawal at the emergency threshold keep it bounded. The
always-on P2P hub is shared product infrastructure and is not counted here.

## After the pilot

Use aggregate request metrics, walkthrough completion, policy blocks, reset failures,
resource peaks, egress, and cost to choose among: keeping the anonymous two-instance
sandbox and tuning the feature set; replacing it with a cheaper read-only static tour
if the live controls are rarely used; adding guided unpaired snapshots if live
pairing proves repeatedly valuable; or building per-visitor ephemeral pairs only if
simultaneous public demand justifies the control-plane and abuse-prevention work.
This pilot is not evidence that Yaffo itself should gain accounts or become
multi-tenant — demo mode constrains one disposable deployment; Yaffo's local-first,
device-key sharing model is unchanged.