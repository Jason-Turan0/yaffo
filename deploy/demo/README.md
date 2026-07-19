# Two-instance public demo

This directory owns the application side of the demo deployment:

- one Yaffo image that runs Waitress and the P2P engine in demo mode;
- isolated source (`demo-a`) and receiver (`demo-b`) containers;
- Caddy as the only published service;
- a reviewed static walkthrough served directly by Caddy; and
- operator scripts for image delivery and emergency withdrawal.

The application containers never receive the Docker socket and publish no host
ports. They run as UID/GID `10001`, with a read-only root filesystem, dropped
capabilities, `no-new-privileges`, bounded memory/CPU/PIDs, four Waitress threads,
and a size-limited temporary filesystem. Each device has separate database,
media, Flask-secret, and P2P-identity mounts.

## Local Compose smoke environment

Docker must be running. Initialize ignored runtime directories and independent
secrets, then build/start the stack:

```bash
deploy/demo/init-local.sh
docker compose --env-file deploy/demo/.env \
  -f deploy/demo/compose.local.yml up --build
```

Open:

- `http://demo.localhost:8080` — static walkthrough
- `http://demo-a.localhost:8080` — source instance
- `http://demo-b.localhost:8080` — receiver instance

On Linux, make the writable bind mounts owned by the container user if they were
created by root:

```bash
sudo chown -R 10001:10001 \
  deploy/demo/runtime/a deploy/demo/runtime/b \
  deploy/demo/runtime/a-identity deploy/demo/runtime/b-identity
```

Stop the local stack with:

```bash
docker compose --env-file deploy/demo/.env \
  -f deploy/demo/compose.local.yml down
```

### Seeding and the golden state

The fixture mounts start empty. Seed both devices — indexing, people/faces,
the Chicago Weekend album, the Florida Trip page, the kid-photo automation, a
custom theme, and cross-device pairing/grants — with:

```bash
deploy/demo/seed-local.sh
```

This uses the Bennett/Obama fixtures from `yaffo_ui_tests/test_data` (Bennett
is synthetic; Obama is real, public-domain National Archives photography, see
`yaffo_ui_tests/test_data/obama/ATTRIBUTION.md`) as a placeholder for the
purpose-built synthetic library a public deployment needs — never copy this
content into a public deployment as-is.

Once seeding looks right, freeze it as the golden state both devices reset to:

```bash
deploy/demo/save-golden.sh
```

Restore that golden state at any time — this is also the daily reset a real
deployment runs on a schedule. It stops both apps, atomically swaps each
device's data directory back to the golden copy (identity keys are a separate
volume and are never touched), restarts, and smoke-tests both:

```bash
deploy/demo/reset-local.sh
```

`reset-local.sh` is safe to interrupt and re-run: each device's swap goes
through a staging directory, and a self-heal check at the top of the script
completes (never reverts) any swap a previous run didn't finish.

## Runtime image

The multi-stage root [`Dockerfile`](../../Dockerfile) builds Python wheels, installs
only runtime libraries, and downloads ExifTool, ffmpeg, InsightFace, and CLIP into
`/opt/yaffo-assets` at image-build time. Demo startup does not run task workers,
the file watcher, the periodic dispatcher, or asset downloads.

For production, `build-and-push.sh` builds `linux/amd64`, pushes a unique tag to
the Terraform-managed registry, and prints its immutable digest reference:

```bash
deploy/demo/build-and-push.sh v0.1.0-demo.1
```

## GCP infrastructure (Terraform)

The `.tf` files in this directory provision the demo VM. Modeled on
[`deploy/hub`](../hub/README.md) — the pattern actually run in production —
rather than reinventing one: the default VPC with an explicit tag-scoped SSH
deny (not a dedicated VPC), a static admin SSH key the startup script installs
directly (not OS Login, which proved unreliable for the hub), and an
idempotent startup script that finishes the job, including starting the
containers.

What gets created:

| Resource | Name | Purpose |
|---|---|---|
| Static external IP | `yaffo-demo-ip` | All three hostnames point here; survives VM replacement |
| Firewall rules | `yaffo-demo-ingress`, `yaffo-demo-iap-ssh`, `yaffo-demo-deny-ssh` | 80+443/tcp (Caddy); SSH via IAP only, explicitly denied otherwise |
| VM | `yaffo-demo` | `e2-medium`, Shielded, Container-Optimized OS; startup script starts Docker Compose |
| Persistent disk | `yaffo-demo-data` | Isolated A/B data, identity, and fixture trees |
| Artifact Registry | `yaffo-demo` | Digest-addressed images, bounded retention |
| Service account | `yaffo-demo-runtime` | Least-privilege: pull images, write logs/metrics |
| Schedule | `yaffo-demo-daily-schedule` | Daily start/stop (operating hours) |
| Budget | "Yaffo public demo" | 50/80/100% + forecast email alerts |

### Prerequisites

1. Terraform (>= 1.5) and the Google Cloud CLI, authenticated for Terraform:
   `gcloud auth application-default login`.
2. Project and billing-account permissions for Compute Engine, Artifact
   Registry, project IAM, Monitoring channels, and Billing Budgets.
3. A domain you own, for the three exact hostnames (see
   `terraform.tfvars.example`).
4. The SHA-256 published for the pinned Linux x86-64 Docker Compose release.
5. A dedicated automation SSH key (see "SSH access" below) — generate it
   before the first `apply` so its public half can go in `terraform.tfvars`.

### Apply

```bash
cd deploy/demo
ssh-keygen -t ed25519 -N "" -f yaffo-demo-admin-key -C yaffo-demo-automation
cp terraform.tfvars.example terraform.tfvars
# Fill in project/billing/domains/checksum, and admin_ssh_pubkey from the .pub above.
terraform init
terraform plan      # read this carefully
terraform apply
```

Create the three A records printed by `terraform output dns_a_records` at your
registrar (no wildcard or catch-all record).

### Deploy the containers

Build and push Yaffo for `linux/amd64`, then deploy immutable image references:

```bash
./build-and-push.sh v0.1.0-demo.1
./deploy.sh \
  'us-central1-docker.pkg.dev/PROJECT/yaffo-demo/yaffo-demo@sha256:…' \
  'caddy:2.10.2-alpine@sha256:…'
```

`deploy.sh` copies the Compose/Caddy/walkthrough bundle to `/var/lib/yaffo-demo/deploy`
through IAP, authenticates Docker with the VM's service account, pulls the
exact digests, and brings the stack up. It never touches the golden fixture or
identity trees. The same bundle is what the VM's startup script brings back up
on every subsequent boot (including the daily Cloud Scheduler start) — run
`deploy.sh` again after any code or Caddy/walkthrough change.

The Compose plugin the startup script installs is version- and
checksum-pinned, used only as an operator tool, and is not mounted into any
application container (Container-Optimized OS ships Docker but no
general-purpose package manager).

### SSH access

Port 22 is closed to the internet (a tag-scoped deny overrides the default
VPC's `default-allow-ssh`); the only way in is an IAP tunnel **with the
dedicated automation key**:

```bash
gcloud compute ssh yaffo-demo --zone=us-central1-a --project=<project_id> --tunnel-through-iap --ssh-key-file=deploy/demo/yaffo-demo-admin-key
```

(`terraform output -raw ssh_command` prints this with your values filled in.)
The key is a dedicated, gitignored, passphrase-less automation key — a
personal key's passphrase fails *silently* in a non-interactive shell and
reports a misleading `Permission denied (publickey)`. If the key is lost,
generate a fresh one, put its `.pub` contents in `terraform.tfvars` as
`admin_ssh_pubkey`, and `terraform apply` (the VM is stateless application-side;
its data disk is untouched by an instance replacement).

### Golden state and reset in production

`deploy/demo/seed-local.sh`, `save-golden.sh`, and `reset-local.sh` (above)
are local-only today — they target `compose.local.yml` and the local
`runtime/`/`golden/` directories. Seeding and freezing the golden state on the
production disk (`/var/lib/yaffo-demo/{a,b}`), and wiring the startup script to
restore it on every boot (the design's actual daily reset mechanism), is not
yet built. Until then, a fresh or rebooted production VM starts empty.

## Emergency withdrawal

The emergency command is deliberately separate from every public hostname. It
disables the public firewall rule first and then stops the VM:

```bash
deploy/demo/emergency-stop.sh --confirm
```

A reviewed `terraform apply` restores the declared ingress rule.
