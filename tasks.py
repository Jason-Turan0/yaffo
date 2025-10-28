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
        "FLASK_APP": "yaffo.app:create_app",
        "FLASK_ENV": "development" if debug else "production",
        "FLASK_DEBUG": "1" if debug else "0"
    }
    c.run(f"flask run --host={host} --port={port}", env=env, pty=True)


@task
def start_tasks(c, workers=4, worker_type="process"):
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
        f"huey_consumer.py yaffo.background_tasks.main.huey -w {workers} -k {worker_type}",
        pty=True
    )


@task
def test(c, verbose=False, coverage=False, path="tests", k=None, failed=False, markers=None):
    """
    Run tests using pytest.

    Args:
        verbose: Verbose output (default: False)
        coverage: Generate coverage report (default: False)
        path: Path to tests directory or specific test file (default: tests)
        k: Run tests matching expression (e.g., -k test_name)
        failed: Run only previously failed tests (default: False)
        markers: Run tests with specific markers (e.g., unit, integration, slow)

    Example:
        inv test
        inv test --verbose
        inv test --coverage
        inv test --path=tests/yaffo/utils
        inv test --path=tests/yaffo/utils/test_index_photos.py
        inv test -k test_write_metadata
        inv test --failed
        inv test --markers=unit
        inv test --verbose --coverage
    """
    cmd_parts = ["python", "-m", "pytest"]

    if verbose:
        cmd_parts.append("-v")

    if coverage:
        cmd_parts.extend(["--cov=yaffo", "--cov-report=html", "--cov-report=term-missing"])

    if k:
        cmd_parts.append(f"-k {k}")

    if failed:
        cmd_parts.append("--lf")

    if markers:
        cmd_parts.append(f"-m {markers}")

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
    from yaffo.common import DB_PATH

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
    c.run("python -m yaffo.scripts.init_db", pty=True)
    print("Database initialized")


@task
def index_photos(c):
    """
    Index all photos in the media directory.

    Example:
        inv index-photos
    """
    print("Indexing photos...")
    c.run("python -m yaffo.scripts.index_photos", pty=True)
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


@task
def profile_index_photos(c, photos=10, name=None, show_history=False, history_limit=10):
    """
    Profile the performance of the index_photo_task background job.

    This task profiles the index_photo_task function and tracks:
    - Execution time and throughput
    - Memory usage
    - Face detection metrics
    - Historical performance trends

    Args:
        photos: Number of photos to profile (default: 10)
        name: Custom name for this profile run (default: timestamp)
        show_history: Display performance history and exit (default: False)
        history_limit: Number of historical runs to display (default: 10)

    Example:
        inv profile-index-photos
        inv profile-index-photos --photos=20 --name=baseline
        inv profile-index-photos --show-history
        inv profile-index-photos --show-history --history-limit=20
    """
    cmd_parts = ["python", "-m", "yaffo.scripts.profile_index_photo"]

    if show_history:
        cmd_parts.append("--show-history")
        cmd_parts.append(f"--history-limit={history_limit}")
    else:
        cmd_parts.append(f"--photos={photos}")
        if name:
            cmd_parts.append(f"--name={name}")

    cmd = " ".join(cmd_parts)
    print(f"Running: {cmd}")
    c.run(cmd, pty=True)