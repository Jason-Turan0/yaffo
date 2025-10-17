from invoke import task


@task
def start_app(c, host="127.0.0.1", port=5000, debug=True):
    """
    Start the Flask web application.

    Args:
        host: Host to bind to (default: 127.0.0.1)
        port: Port to bind to (default: 5000)
        debug: Run in debug mode (default: True)

    Example:
        inv start-app
        inv start-app --host=0.0.0.0 --port=8000
    """
    print(f"Starting Flask app on {host}:{port} (debug={debug})")
    env = {
        "FLASK_APP": "photo_organizer.app:create_app",
        "FLASK_ENV": "development" if debug else "production",
        "FLASK_DEBUG": "1" if debug else "0"
    }
    c.run(f"flask run --host={host} --port={port}", env=env, pty=True)


@task
def start_tasks(c, workers=8, worker_type="process"):
    """
    Start the Huey task consumer for background job processing.

    Args:
        workers: Number of worker processes/threads (default: 8)
        worker_type: Worker type - 'process' or 'thread' (default: process)

    Example:
        inv start-tasks
        inv start-tasks --workers=4 --worker-type=thread
    """
    print(f"Starting Huey consumer with {workers} {worker_type} workers")
    c.run(
        f"huey_consumer.py photo_organizer.background_tasks.main.huey -w {workers} -k {worker_type}",
        pty=True
    )


@task
def test(c, verbose=False, coverage=False, path="tests"):
    """
    Run tests using pytest.

    Args:
        verbose: Verbose output (default: False)
        coverage: Generate coverage report (default: False)
        path: Path to tests directory (default: tests)

    Example:
        inv test
        inv test --verbose
        inv test --coverage
        inv test --path=tests/unit
    """
    cmd_parts = ["pytest"]

    if verbose:
        cmd_parts.append("-v")

    if coverage:
        cmd_parts.extend(["--cov=photo_organizer", "--cov-report=html", "--cov-report=term"])

    cmd_parts.append(path)

    cmd = " ".join(cmd_parts)
    print(f"Running: {cmd}")
    c.run(cmd, pty=True)


@task
def migrate(c, migration=None):
    """
    Run database migrations.

    Args:
        migration: Specific migration file to run (default: all pending migrations)

    Example:
        inv migrate
        inv migrate --migration=002_add_location_and_tags.sql
    """
    from pathlib import Path
    import sqlite3
    from photo_organizer.common import DB_PATH

    migrations_dir = Path("migrations")

    if not migrations_dir.exists():
        print(f"Error: migrations directory not found at {migrations_dir}")
        return

    if migration:
        migration_path = migrations_dir / migration
        if not migration_path.exists():
            print(f"Error: migration file not found: {migration_path}")
            return
        migrations_to_run = [migration_path]
    else:
        migrations_to_run = sorted(migrations_dir.glob("*.sql"))

    if not migrations_to_run:
        print("No migrations to run")
        return

    print(f"Running migrations on database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)

    for migration_file in migrations_to_run:
        print(f"Applying migration: {migration_file.name}")
        with open(migration_file, 'r') as f:
            sql = f.read()

        try:
            conn.executescript(sql)
            print(f"  ✓ {migration_file.name} applied successfully")
        except sqlite3.Error as e:
            print(f"  ✗ Error applying {migration_file.name}: {e}")

    conn.close()
    print("Migration complete")


@task
def init_db(c):
    """
    Initialize the database with all tables.

    Example:
        inv init-db
    """
    print("Initializing database...")
    c.run("python -m photo_organizer.scripts.init_db", pty=True)
    print("Database initialized")


@task
def index_photos(c):
    """
    Index all photos in the media directory.

    Example:
        inv index-photos
    """
    print("Indexing photos...")
    c.run("python -m photo_organizer.scripts.index_photos", pty=True)
    print("Indexing complete")


@task
def kill_python(c, force=False):
    """
    Kill all Python processes (useful for cleaning up hung workers).

    Args:
        force: Skip confirmation prompt (default: False)

    Example:
        inv kill-python
        inv kill-python --force
    """
    import sys

    if not force:
        response = input("⚠️  This will kill ALL Python processes. Continue? [y/N]: ")
        if response.lower() != 'y':
            print("Aborted.")
            return

    print("Killing all Python processes...")

    # Get current process ID to avoid killing ourselves immediately
    current_pid = sys.argv[0]

    try:
        # pkill is more reliable than killall on macOS
        c.run("pkill -9 python", warn=True)
        c.run("pkill -9 Python", warn=True)
        print("All Python processes terminated")
    except Exception as e:
        print(f"Error: {e}")