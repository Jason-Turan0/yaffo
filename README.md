# Yaffo - Photo Organizer

A Flask-based photo organization tool that uses EXIF metadata, face recognition, and duplicate detection to automatically organize and index photos.

## Features

- **Photo Organization**: Automatically organize photos by date using EXIF metadata
- **Face Detection**: Detect and recognize faces using dlib + face_recognition
- **Face Tagging**: Tag and group faces by person
- **Duplicate Detection**: Find duplicate photos using perceptual hashing
- **EXIF Metadata**: Extract and display photo metadata
- **Location Support**: Geocoding and location-based photo filtering

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

### 3. Configure Data Directory

Edit `yaffo/common.py` to set your photo directory, or set the environment variable:

```bash
export YAFFO_DATA_DIR=/path/to/your/photos
```

The directory should contain:
- `organized/` - Your photo files
- `thumbnails/` - Generated thumbnails (created automatically)

### 4. Run the Application

```bash
# Start the Flask web app
flask run

# In a separate terminal, start the background task worker
huey_consumer yaffo.background_tasks.config.huey
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
│   ├── background_tasks/    # Huey task queue
│   └── scripts/             # CLI tools
├── tests/                   # Python unit tests
├── yaffo_ui_tests/          # Playwright UI tests
├── hooks/                   # Git hooks
├── migrations/              # Database migrations
└── resources/               # ExifTool binaries
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

Background tasks (photo indexing, face detection, etc.) are handled by [Huey](https://huey.readthedocs.io/):

```bash
# Start the task consumer
huey_consumer yaffo.background_tasks.config.huey --workers 2

# Or for development (immediate mode)
huey_consumer yaffo.background_tasks.config.huey --immediate
```

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
| `YAFFO_DATA_DIR` | Root directory for photos and database | `/Users/{CURRENT_USER}/Pictures` |
| `FLASK_APP` | Flask application module | `yaffo.app:create_app`           |
| `FLASK_ENV` | Flask environment | `development`                    | 
| `ANTHROPIC_API_KEY` | API key for AI test generation | (required for test generation)   |

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

## License

[Add license information]