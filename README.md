# Yaffo - Photo Organizer

A Flask-based photo organization tool that uses EXIF metadata, face recognition, and duplicate detection to automatically organize and index photos.

## Features

- **Photo Organization**: Automatically organize and index photos by date using EXIF metadata
- **Face Detection & Recognition**: Detect, group, and tag faces by person using InsightFace (SCRFD detection + ArcFace embeddings, on ONNX Runtime)
- **Duplicate Detection**: Find duplicate photos using perceptual hashing
- **EXIF Metadata**: Extract and display photo metadata
- **Location Support**: Geocoding, reverse-geocoding, and time-correlation geotagging from neighboring photos
- **Auto-Labeling**: Offline zero-shot classification (CLIP) tags photos against a user vocabulary
- **Automations**: Scheduled and event-driven background behaviors (system-built and AI-generated)
- **AI Page Builder**: Build custom pages from AI-generated, sandboxed widgets over your own photo data

## Prerequisites

- Python 3.13+
- Node.js 18+ (for UI tests)
- ExifTool (see [EXIFTOOL_SETUP.md](EXIFTOOL_SETUP.md))

## Quick Start

### 1. Clone and Setup

```bash
git clone <repository-url>
cd yaffo

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .
```

### 2. Install Git Hooks

```bash
./hooks/install.sh
```

This installs pre-commit hooks that prevent accidental commit of API keys and secrets.

### 3. Configure the Data Directory

`YAFFO_DATA_DIR` is the **root for the app's own state** — the SQLite databases
(`yaffo.db`, `yaffo-queue.db`), generated thumbnails, logs, and temp/trash. It
defaults to the OS user-data dir (e.g. `~/Library/Application Support/yaffo` on
macOS); set it to override:

```bash
export YAFFO_DATA_DIR=/path/to/data
```

Your actual **photo (media) directories** are configured separately, in the app's
**Settings** page — they aren't a fixed subfolder. The file sync / watcher scans
the configured media dirs and indexes what it finds.

### 4. Run the Application

```bash
# Start the Flask web app
flask run

# In a separate terminal, start the background task host (spawn worker pool)
python -m yaffo.taskq.host

# Or launch the whole local stack (Flask + task host + watcher) at once
inv app-local
```

The app will be available at http://127.0.0.1:5000

## Project Structure

```
yaffo/
├── yaffo/                    # Main application
│   ├── app.py               # Flask app factory
│   ├── common.py            # Configuration and paths
│   ├── db/                  # Database models and repositories
│   ├── routes/              # API endpoints
│   ├── templates/           # Jinja2 templates
│   ├── static/              # CSS, JavaScript
│   ├── utils/               # Utility functions
│   ├── background_tasks/    # Background task definitions
│   ├── taskq/               # SQLite-backed task queue + spawn worker host
│   ├── site_agents/         # AI page builder: agent, model clients, tools, schemas
│   └── scripts/             # CLI tools + db/ (init_db, dev migrations)
├── tests/                   # Python unit tests
├── yaffo_ui_tests/          # Playwright UI tests
├── hooks/                   # Git hooks
├── docs/                    # Design references (taskq, automations, page builder, video)
└── resources/               # ExifTool binaries + bundled face models
```

## Development

### Running Tests

```bash
# Python unit tests
pytest

# UI tests (requires app running)
cd yaffo_ui_tests
npm install
npm test

# UI tests in isolated environment
npm run test:isolated
```

### Database

The app uses SQLite. The database file is stored at `{YAFFO_DATA_DIR}/yaffo.db`.

To reset the database:
```bash
rm /path/to/data/yaffo.db
flask run  # Will recreate on startup
```

### Background Tasks

Background tasks (photo indexing, face detection, etc.) run on a small
purpose-built queue (`yaffo/taskq`): a SQLite-durable queue plus a host process
that supervises a pool of `spawn`-started worker children, so CPU-bound native ML
inference (InsightFace/ONNX Runtime) runs in parallel, isolated, and
crash-contained. See `docs/development/task-queue.md`.

```bash
# Start the task host with 4 workers, recycling each after 200 tasks
python -m yaffo.taskq.host --workers 4 --recycle 200

# Or via invoke
inv start-tasks --workers=4
```

Tests drive tasks synchronously by setting `task_queue.immediate = True`.

## UI Testing

The project includes an AI-augmented UI testing framework. See [yaffo_ui_tests/README.md](yaffo_ui_tests/README.md) for details.

```bash
cd yaffo_ui_tests

# Install dependencies
npm install
npx playwright install

# Run tests against running app
npm test

# Run tests in isolated environment (recommended)
npm run test:isolated
```

## Environment Variables

| Variable | Description | Default                          |
|----------|-------------|----------------------------------|
| `YAFFO_DATA_DIR` | Root for app state (DBs, thumbnails, logs, temp) | OS user-data dir (e.g. `~/Library/Application Support/yaffo`); `inv` sets `~/Pictures` for dev |
| `FLASK_APP` | Flask application module | `yaffo.app:create_app`           |
| `FLASK_ENV` | Flask environment | `development`                    |
| `ANTHROPIC_API_KEY` | Anthropic API key for the AI Page Builder and AI-generated automations. Optional override — the key is normally stored in the OS keychain (set via Settings); the env var wins when present (headless/CI). | (unset; keychain used) |

## Git Hooks

The project includes pre-commit hooks to prevent accidental commit of secrets:

```bash
# Install hooks (run once after cloning)
./hooks/install.sh
```

The pre-commit hook scans for:
- Anthropic API keys (`sk-ant-...`)
- OpenAI API keys (`sk-...`)
- Generic API key patterns

## Licenses

- [LICENSE](LICENSE)
- [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES.txt)