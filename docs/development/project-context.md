# Project Context

Yaffo is a Flask-based photo organization tool that uses EXIF metadata, face
recognition, duplicate detection, and offline ML classification to organize and
index media.

## Architecture

The app package is `yaffo/`

- Flask web app: `yaffo/app.py`
- Database: SQLAlchemy with SQLite in `yaffo/db/`
- Routes: REST and HTMX endpoints in `yaffo/routes/`
- Background tasks: task definitions in `yaffo/background_tasks/tasks/`, run on
  the `yaffo/taskq` SQLite queue and spawn worker host. See
  [Task Queue Standards](task-queue.md).
- Automations: scheduled and event-driven behaviors in `yaffo/background_tasks/`.
  See [Automations](automations.md).
- AI page builder: agent, model clients, tools, and schemas in
  `yaffo/site_agents/`. See [AI Page Builder](ai-page-builder.md).
- Runtime packaged scripts: `yaffo/scripts/`, including database initialization,
  development migrations, and `seed_automations.py`.

## Key Components

Database models live in `yaffo/db/models.py`.

- `Photo`, `Face`, `Person`, `Tag` - core media, people, and tagging.
- `PhotoLabel`, `ClassificationLabel` - CLIP auto-labeling.
- `Automation`, `AutomationTrigger` - automation definitions and run history via
  `Job`.
- `CustomPage`, `PageVersion`, `Widget`, `Conversation` - AI page builder.
- `Job`, `JobResult` - background-job progress and results.

## Face Recognition

Yaffo uses InsightFace: SCRFD detection plus ArcFace 512-dimensional embeddings
on ONNX Runtime. Cosine similarity is used over embeddings.

Do not introduce `dlib` or the `face_recognition` package for new work. The old
dlib stack was slower and less accurate; see `benchmarks/face/README.md`.

## Routes

- `/photos`, `/faces`, `/people` - media, faces, and people management.
- `/pages` - AI page builder.
- `/utilities/automations`, `/settings`, `/home` - admin and UI pages.

## Technologies

- Face recognition: InsightFace, SCRFD, ArcFace, ONNX Runtime.
- Image processing: Pillow and OpenCV.
- Duplicate detection: ImageHash perceptual hashing.
- Auto-labeling: offline CLIP zero-shot classification.
- EXIF and metadata: exiftool primary, piexif where applicable.
- Database: SQLAlchemy and SQLite.
- Web UI: Flask, Jinja, HTMX, and namespaced JavaScript modules.
- AI: Anthropic-backed generation for page builder, automation, and themes.

## Development Setup

- Python 3.13.
- Virtual environment: `./venv`.
- Activate: `source activate_venv.sh`.
- Install package: `pip install -e .`.
- Development extras are declared in `pyproject.toml` under
  `[project.optional-dependencies] dev`.

## Database Migrations

Add schema changes as the next numbered module in
`yaffo/scripts/db/migrations/`.

Migration rules:

- Keep migrations additive when possible.
- Never commit inside `migrate(conn)`.
- Prefer normalizing existing data in migrations over adding runtime
  compatibility branches for old persisted shapes.
- Application code should target the current schema and config shape after
  migrations have run.
- Also update `000_MIGRATION_20260620_INIT.py` so a fresh database receives the
  current schema directly.

After creating a migration, automatically run the standard migration runner
against the development database used by Invoke tasks. `tasks.py` sets
`YAFFO_DATA_DIR=~/Pictures`, so use the same data directory:

```shell
YAFFO_DATA_DIR="$HOME/Pictures" ./venv/bin/python -m yaffo.scripts.db.migrate
```

Then verify:

- the migration is recorded in `schema_migrations`;
- the schema change exists in `~/Pictures/yaffo.db`.

Do not omit `YAFFO_DATA_DIR` and do not substitute a temporary directory for this
required development-database run.
