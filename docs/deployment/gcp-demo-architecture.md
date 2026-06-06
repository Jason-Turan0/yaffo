# GCP Demo Deployment Architecture

Target: host Yaffo on GCP for a disposable public demo, while keeping the app's
local-first design intact. The data (photos + SQLite DBs) is disposable and the
environment is wiped on a routine basis.

## Why a VM, not serverless

Yaffo is a stateful, filesystem-bound app:

- The web app and a long-running **huey** background worker both read/write the
  **same** `ROOT_DIR` — photos, thumbnails, and two SQLite files (`yaffo.db`,
  `yaffo-huey.db`). SQLite shared across two processes needs real POSIX file
  locking.
- Jobs (face recognition, indexing, clustering) are CPU-heavy and run for
  minutes — they exceed function time/memory limits.
- Heavy native deps (`dlib`, `face-recognition`, `opencv`) plus the `exiftool`
  binary make the image large with slow cold starts.

Cloud Run / Cloud Functions (GCP's "Fargate-like" serverless options) are a poor
fit unless SQLite→Postgres and huey→Redis migrations are done first, because
they can't share a writable local volume and GCS-FUSE mounts lack file locking.
So the faithful target is a **container running on a Compute Engine VM**.

## Chosen architecture: single-container GCE VM

```
┌─────────────────────────────────────────────────────────┐
│ GCE VM (Container-Optimized OS, e.g. e2-standard-4)       │
│                                                           │
│   ┌─────────────────────────────────────────────┐        │
│   │ yaffo container (single image)                │        │
│   │   supervisor / entrypoint runs BOTH:          │        │
│   │     - gunicorn  yaffo.app:create_app()  :8080 │        │
│   │     - huey_consumer  (-w 1 for demo)          │        │
│   │   runs as non-root, root FS read-only         │        │
│   └───────────────┬───────────────────────────────┘        │
│                   │ YAFFO_DATA_DIR=/data                    │
│           ┌───────▼────────┐                                │
│           │ Persistent Disk │  /data (organized, thumbnails,│
│           │  mounted /data  │  temp, duplicates, *.db)       │
│           └────────────────┘                                │
└─────────────────────────────────────────────────────────┘
```

- **Single container**: web + worker collapsed into one image via a supervisor
  (or an entrypoint that backgrounds the worker then execs gunicorn). Loses
  independent scaling — not needed for a personal/demo tool — and gains clean
  COS single-container deployment and a clear path to Cloud Run later.
- **Image** built for `linux/amd64` (important on Apple-silicon Macs) and pushed
  to **Artifact Registry**.
- **Persistent disk** mounted at `/data`; `YAFFO_DATA_DIR=/data`. Replaces the
  hardcoded `/Users/jason.turan/Pictures` default in `yaffo/common.py`. The data
  subdirs (`organized/`, `thumbnails/`, `temp/`, `duplicates/`) are not
  auto-created by the app, so the entrypoint/startup script must create them.

## Two access modes

| Mode | How | Notes |
|---|---|---|
| **Personal** | IAP TCP tunnel, no public IP | `gcloud compute start-iap-tunnel`; Google-account gated; zero auth code; start/stop VM to save cost |
| **Demo (anonymous)** | Static external IP + firewall 80/443 + **Caddy** reverse proxy with auto Let's Encrypt TLS | Needs a domain A-record; gunicorn never exposed directly |

## App hardening required before anonymous exposure

Data is disposable, so confidentiality/integrity of the photos is a non-issue.
What matters: escaping the data sandbox, compute abuse, and host/secret leakage.

- **Path containment** — reject any user-supplied path not inside `ROOT_DIR`
  (`is_relative_to` check). Affects `/photo-by-path` (`routes/photos.py`),
  `remove_duplicates`, `organize_photos`, and the `settings` media/thumbnail dir
  endpoints.
- **Remove dangerous routes** for the server build: `/api/open-file`,
  `/api/open-folder` (shell out to `open`/`xdg-open`), and ideally
  `/photo-by-path` (arbitrary file read → leaks `SECRET_KEY` via
  `/proc/self/environ`, etc.). Prefer DB-backed `/photos/<id>`.
- **Secrets & debug** — set a real `SECRET_KEY` env var; never run
  `app.run(debug=True)` (Werkzeug debugger = RCE). gunicorn bypasses `__main__`,
  but gate debug behind an env flag anyway.
- **Compute abuse / DoS** — add `Flask-Limiter` on job-triggering POST routes;
  run worker with `-w 1`; set `MAX_CONTENT_LENGTH`; add Caddy/Cloud Armor edge
  rate limiting.
- **Container defense-in-depth** — non-root user, `--read-only` root FS with a
  `tmpfs` for `/tmp`, data volume the only writable mount, drop extra caps.

## Deployment outline

1. **Setup environment**: GCP project, enable APIs, Artifact Registry repo,
   persistent disk, COS VM.
2. **Deployment scripts**: Dockerfile (multi-stage; build dlib wheels, slim
   runtime with `libgl1`, `libglib2.0-0`, `libimage-exiftool-perl`), supervisor
   config / entrypoint, `startup.sh` (mount+format disk, create subdirs, docker
   login, run container), `gcloud` build + deploy commands.
3. **Personal access**: IAP tunnel, no public IP.
4. **Harden app**: items above.
5. **Demo anonymous access**: static IP, firewall, Caddy TLS + rate limiting.