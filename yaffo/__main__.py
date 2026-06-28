"""Entry point for `python -m yaffo` and the packaged (PyInstaller) app.

One frozen binary plays three roles, selected by the YAFFO_ROLE env var, because a
PyInstaller app's `sys.executable` is the app itself and its bootloader ignores
`-m`/`-c` — so a child process is started by re-executing this same entry with a
role set, not by invoking a module path:

- (unset) "web": serve the Flask app via waitress (a production WSGI server, not
  the Flask dev server), run migrations, and supervise the host + watcher children.
- "host":    run the task-queue host (which itself spawns workers via multiprocessing).
- "watcher": run the filesystem watcher.

`multiprocessing.freeze_support()` must run first: in a spawned task worker the
bootloader/freeze_support intercept startup and run the worker, never reaching main().
"""
from __future__ import annotations

import atexit
import multiprocessing
import os
import subprocess
import sys
import threading
import webbrowser

from yaffo.logging_config import get_logger
from yaffo.config import get_int as get_config_int

HOST = "127.0.0.1"
PORT = get_config_int("web", "port", 5001)
WEB_THREADS = 8
WORKERS = max(2, (os.cpu_count() or 4) - 1)
RECYCLE = 100

logger = get_logger(__name__, "webapp")


def _start_asset_downloads() -> None:
    from yaffo.download_assets import (
        download_clip,
        download_exiftool,
        download_ffmpeg,
        download_insightface,
    )

    def run(name: str, fn) -> None:
        try:
            fn()
        except Exception:
            logger.exception("failed to download %s asset package", name)

    for name, fn in (
        ("exiftool", download_exiftool),
        ("insightface", download_insightface),
        ("clip", download_clip),
        ("ffmpeg", download_ffmpeg),
    ):
        threading.Thread(target=run, args=(name, fn), daemon=True, name=f"download-{name}").start()


def _child_cmd() -> list[str]:
    """Command to re-launch this entry for a child role. Frozen: just the app
    binary (args ignored; role travels via env). Dev: `python -m yaffo`."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "yaffo"]


def _start_background() -> list[subprocess.Popen]:
    from yaffo.logging_config import get_logger

    logger = get_logger(__name__, "webapp")
    procs: list[subprocess.Popen] = []
    for role in ("host", "watcher"):
        try:
            procs.append(subprocess.Popen(_child_cmd(), env={**os.environ, "YAFFO_ROLE": role}))
            logger.info(f"started {role}")
        except Exception:
            logger.exception(f"failed to start {role}")
    return procs


def _stop_background(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()


def _create_tray_icon(procs: list[subprocess.Popen], url: str):
    """Create a cross-platform system tray icon. The icon run loop is started later,
    after the web server is listening in a background thread."""
    import pystray
    from PIL import Image
    from yaffo.common import RESOURCES_DIR

    icon_path = RESOURCES_DIR / "branding" / "menubar.png"
    image = (
        Image.open(icon_path).convert("RGBA")
        if icon_path.exists()
        else Image.new("RGBA", (44, 44), (0, 0, 0, 0))
    )

    def open_yaffo(icon, item) -> None:
        webbrowser.open(url)

    def quit_yaffo(icon, item) -> None:
        _stop_background(procs)
        icon.stop()

    return pystray.Icon(
        "yaffo",
        image,
        "Yaffo",
        menu=pystray.Menu(
            pystray.MenuItem("Open Yaffo", open_yaffo, default=True),
            pystray.MenuItem("Quit Yaffo", quit_yaffo),
        ),
    )


def _run_web() -> None:
    from waitress import serve
    from yaffo.app import create_app
    from yaffo.scripts.db.migrate import run_migrations

    run_migrations()
    _start_asset_downloads()
    procs = _start_background()
    atexit.register(_stop_background, procs)

    app = create_app()
    url = f"http://{HOST}:{PORT}"
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    try:
        tray_icon = _create_tray_icon(procs, url)
    except Exception:
        logger.info(f"serving Yaffo at {url} (no system tray)")
        serve(app, host=HOST, port=PORT, threads=WEB_THREADS)
        return

    logger.info(f"serving Yaffo at {url} (system tray)")
    threading.Thread(
        target=serve, args=(app,),
        kwargs={"host": HOST, "port": PORT, "threads": WEB_THREADS},
        daemon=True,
    ).start()
    tray_icon.run()


def _run_host() -> None:
    from yaffo.taskq.host import main as host_main

    host_main()


def _run_watcher() -> None:
    from yaffo.background_tasks.watcher import main as watcher_main

    watcher_main()


def main() -> None:
    role = os.environ.get("YAFFO_ROLE", "web")
    if role == "host":
        _run_host()
    elif role == "watcher":
        _run_watcher()
    else:
        _run_web()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
