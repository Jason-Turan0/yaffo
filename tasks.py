from invoke import task
from pathlib import Path
from urllib.request import urlopen
import json
import os
import platform
import subprocess
import threading
import time


VENDOR_DIR = Path("yaffo/static/vendor")
VENDOR_MANIFEST = VENDOR_DIR / "manifest.json"

# Where the dev photo library + DB live. common.py reads YAFFO_DATA_DIR; we set it
# explicitly here so every process in the local stack agrees regardless of the shell.
YAFFO_DATA_DIR = str(Path.home() / "Pictures")


def _data_env():
    return {"YAFFO_DATA_DIR": YAFFO_DATA_DIR}


def _flask_command(host, port):
    return f"flask run --host={host} --port={port}"


def _flask_env(debug):
    return {
        "FLASK_APP": "yaffo.app:create_app",
        "FLASK_ENV": "development" if debug else "production",
        "FLASK_DEBUG": "1" if debug else "0",
        **_data_env(),
    }


def _host_command(workers, recycle):
    return f"python -m yaffo.taskq.host -w {workers} -r {recycle}"


def _watcher_command():
    return "python -m yaffo.background_tasks.watcher"


def _open_chrome(url, delay=2):
    system = platform.system()
    if system == "Darwin":
        chrome_cmd = f'sleep {delay} && open -na "Google Chrome" --args --incognito {url}'
    elif system == "Windows":
        chrome_cmd = f'timeout /t {delay} /nobreak && start chrome --incognito {url}'
    else:
        chrome_cmd = f'sleep {delay} && google-chrome --incognito {url}'

    subprocess.Popen(chrome_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Chrome will open in incognito mode at {url}")


def _run_concurrently(processes):
    """
    Launch (label, command, env) tuples as concurrent subprocesses, prefix each
    line of their merged output with the label, and tear them all down together
    on Ctrl+C or when any one of them exits.
    """
    procs = []
    for label, command, env in processes:
        full_env = {**os.environ, **(env or {})}
        proc = subprocess.Popen(
            command,
            shell=True,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
        )
        procs.append((label, proc))

    def stream(label, proc):
        for line in proc.stdout:
            print(f"[{label}] {line}", end="", flush=True)

    for label, proc in procs:
        threading.Thread(target=stream, args=(label, proc), daemon=True).start()

    try:
        while all(proc.poll() is None for _, proc in procs):
            time.sleep(0.5)
        exited = next(label for label, proc in procs if proc.poll() is not None)
        print(f"\n[{exited}] exited — shutting down the rest...")
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        for _, proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for _, proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


@task
def start_app(c, host="127.0.0.1", port=5002, debug=True):
    """
    Start the Flask web application and open Chrome in incognito mode.

    Args:
        host: Host to bind to (default: 127.0.0.1)
        port: Port to bind to (default: 5002 — 5000 collides with macOS AirPlay Receiver)
        debug: Run in debug mode (default: True)

    Example:
        inv start-app
        inv start-app --host=0.0.0.0 --port=8000
    """
    print(f"Starting Flask app on {host}:{port} (debug={debug})")
    _open_chrome(f"http://{host}:{port}")
    c.run(_flask_command(host, port), env=_flask_env(debug), pty=True)


@task
def start_tasks(c, workers=4, recycle=100):
    """
    Start the task-queue host for background job processing.

    The host owns the SQLite queue and supervises a pool of spawn-started worker
    children. Each child runs in its own interpreter, so concurrent dlib indexing
    is isolated and crash-contained: a segfaulting child is recovered without
    taking down the host (the failure mode that forced Huey onto a single thread).

    Args:
        workers: Number of spawn worker processes (default: 4)
        recycle: Recycle each worker after this many tasks, to cap native memory
            growth (default: 100)

    Example:
        inv start-tasks
        inv start-tasks --workers=8 --recycle=200
    """
    print(f"Starting task-queue host with {workers} spawn workers (recycle every {recycle})")
    c.run(_host_command(workers, recycle), env=_data_env(), pty=True)


@task
def start_watcher(c):
    """
    Start the file-system watcher that auto-indexes new photos.

    Watches MEDIA_DIRS and enqueues import/index tasks when files settle.
    Requires the task-queue host (`inv start-tasks`) to be running to do the work.

    Example:
        inv start-watcher
    """
    print("Starting photo watcher...")
    c.run(_watcher_command(), env=_data_env(), pty=True)


@task
def app_local(c, host="127.0.0.1", port=5002, debug=True, workers=4, recycle=100):
    """
    Launch the full local stack at once: the Flask app, the task-queue host, and
    the photo watcher. Output from all three is interleaved with [flask]/[host]/
    [watcher] prefixes. Press Ctrl+C to stop everything together.

    Args:
        host: Host to bind the Flask app to (default: 127.0.0.1)
        port: Port to bind the Flask app to (default: 5002 — 5000 collides with macOS AirPlay Receiver)
        debug: Run Flask in debug mode (default: True)
        workers: Number of spawn worker processes (default: 4)
        recycle: Recycle each worker after this many tasks (default: 100)

    Example:
        inv app-local
        inv app-local --port=8000 --workers=8
    """
    print(f"Starting the full local stack on {host}:{port} (debug={debug})")
    _open_chrome(f"http://{host}:{port}")
    _run_concurrently([
        ("flask", _flask_command(host, port), _flask_env(debug)),
        ("host", _host_command(workers, recycle), _data_env()),
        ("watcher", _watcher_command(), _data_env()),
    ])


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
def migrate(c):
    """
    Apply pending database migrations (creates the DB from scratch on first run).

    Numbered Python migrations live in yaffo/scripts/db/migrations and run in
    order; already-applied ones are skipped. The DB is backed up first.

    Example:
        inv migrate
    """
    print("Running database migrations...")
    c.run("python -m yaffo.scripts.db.migrate", env=_data_env(), pty=True)
    print("Migrations complete")


@task
def init_db(c):
    """
    Initialize the database — alias for `migrate` (the INIT migration builds the
    full schema from scratch).

    Example:
        inv init-db
    """
    migrate(c)


@task
def index_photos(c):
    """
    Index all photos in the media directory.

    Example:
        inv index-photos
    """
    print("Indexing photos...")
    c.run("python -m scripts.index_photos", pty=True)
    print("Indexing complete")


@task(help={"photo": "Path to the photo or video file", "all": "Dump every tag, not just keywords/people/location/date"})
def tags(c, photo, all=False):
    """
    Print the metadata tags of a photo or video file (keywords / people / location / date).

    Examples:
        inv tags /path/to/photo.jpg
        inv tags --all /path/to/clip.mov
    """
    flag = " --all" if all else ""
    c.run(f'python -m scripts.print_photo_tags{flag} "{photo}"', pty=True)


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
def update_vendor(c, lib=None):
    """
    Download vendored front-end libraries declared in
    yaffo/static/vendor/manifest.json into vendor/<lib>/<version>/.

    Bump a version in the manifest, run this task, then point the template at
    the new vendor/<lib>/<version>/ path.

    Args:
        lib: Only update this library (default: all libraries in the manifest)

    Example:
        inv update-vendor
        inv update-vendor --lib=gridstack
    """
    manifest = json.loads(VENDOR_MANIFEST.read_text())

    if lib:
        if lib not in manifest:
            print(f"Error: '{lib}' is not in {VENDOR_MANIFEST} (have: {', '.join(manifest)})")
            return
        manifest = {lib: manifest[lib]}

    for name, spec in manifest.items():
        version = spec["version"]
        target_dir = VENDOR_DIR / name / version
        target_dir.mkdir(parents=True, exist_ok=True)
        print(f"{name} {version}")
        for filename, url_template in spec["files"].items():
            url = url_template.format(version=version)
            destination = target_dir / filename
            print(f"  {url} -> {destination}")
            with urlopen(url) as response:
                destination.write_bytes(response.read())
        print(f"  url_for path: vendor/{name}/{version}/<file>")

    print("Vendor update complete")