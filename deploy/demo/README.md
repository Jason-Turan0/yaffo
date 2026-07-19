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

The fixture mounts start empty. Phase 3 prepares the licensed media, golden
databases, stable pre-paired identities, and grants. Do not copy UI-test or
personal media into a public deployment.

Stop the local stack with:

```bash
docker compose --env-file deploy/demo/.env \
  -f deploy/demo/compose.local.yml down
```

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

## GCP deployment

After `deploy/gcp` has been applied and the three exact DNS records point to its
static IP, pass digest references for both containers:

```bash
deploy/demo/deploy.sh \
  'us-central1-docker.pkg.dev/PROJECT/yaffo-demo/yaffo-demo@sha256:…' \
  'caddy:2.10.2-alpine@sha256:…'
```

The deploy script copies only the Compose/Caddy/walkthrough bundle through IAP,
authenticates Docker with the VM service account, validates the resolved Compose
model, pulls the exact digests, and reconciles the services. It never modifies
the golden fixture or identity trees.

The VM startup script installs a checksum-pinned standalone Compose binary because
Container-Optimized OS includes Docker but no general-purpose package manager.
That binary is an operator tool and is not mounted into the application.

## Emergency withdrawal

The emergency command is deliberately separate from every public hostname. It
disables the public firewall rule first and then stops the VM:

```bash
deploy/demo/emergency-stop.sh --confirm
```

A reviewed `terraform apply` restores the declared ingress rule. The daily reset
and automatic fixture restoration are Phase 3; do not expose the demo publicly
until that reset path and the Phase 4 acceptance suite pass.
