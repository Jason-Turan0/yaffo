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

HOST = "127.0.0.1"
PORT = 5001
WEB_THREADS = 8
WORKERS = max(2, (os.cpu_count() or 4) - 1)
RECYCLE = 100


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


def _run_menubar(procs: list[subprocess.Popen], url: str) -> None:
    """Run a macOS menu-bar item on the main thread (the AppKit run loop that gives
    the app a face and keeps it alive). 'Quit' tears down the host/watcher children
    so nothing is orphaned — the failure mode of the headless, faceless build."""
    import rumps

    from yaffo.common import RESOURCES_DIR

    icon = RESOURCES_DIR / "branding" / "menubar.png"
    icon_kwargs = {"icon": str(icon), "template": False} if icon.exists() else {"title": "📷"}

    class YaffoApp(rumps.App):
        def __init__(self) -> None:
            super().__init__("Yaffo", quit_button=None, **icon_kwargs)
            self.menu = [
                rumps.MenuItem("Open Yaffo", callback=lambda _: webbrowser.open(url)),
                None,
                rumps.MenuItem("Quit Yaffo", callback=self._quit),
            ]

        def _quit(self, _) -> None:
            _stop_background(procs)
            rumps.quit_application()

    YaffoApp().run()


def _run_web() -> None:
    from waitress import serve
    from yaffo.app import create_app
    from yaffo.logging_config import get_logger
    from yaffo.scripts.db.migrate import run_migrations

    logger = get_logger(__name__, "webapp")
    run_migrations()
    procs = _start_background()
    atexit.register(_stop_background, procs)

    app = create_app()
    url = f"http://{HOST}:{PORT}"
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    if sys.platform != "darwin":
        logger.info(f"serving Yaffo at {url} (no menu bar)")
        serve(app, host=HOST, port=PORT, threads=WEB_THREADS)
        return

    try:
        import rumps  # noqa: F401  (probe: present in the bundle, absent in plain dev)
    except Exception:
        # No menu bar in a bare macOS dev run: serve on the main thread.
        logger.info(f"serving Yaffo at {url} (no menu bar)")
        serve(app, host=HOST, port=PORT, threads=WEB_THREADS)
        return

    logger.info(f"serving Yaffo at {url} (menu bar)")
    threading.Thread(
        target=serve, args=(app,),
        kwargs={"host": HOST, "port": PORT, "threads": WEB_THREADS},
        daemon=True,
    ).start()
    _run_menubar(procs, url)


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
