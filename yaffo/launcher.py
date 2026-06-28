"""Console launcher for PyPI installs.

The package entry point should behave like a desktop-app launcher: start Yaffo in
the background, open the browser from the child process, then return the shell to
the user. The actual app runtime remains `python -m yaffo`, which blocks and is
also what PyInstaller freezes.
"""
from __future__ import annotations

import os
import subprocess
import sys

from yaffo.config import get_int as get_config_int
from yaffo.setup import run_setup, run_uninstall
from yaffo.shortcuts import install_shortcut

HOST = "127.0.0.1"
PORT = get_config_int("web", "port", 5001)


def start_app_detached() -> None:
    env = {**os.environ, "YAFFO_LAUNCHED_FROM_CONSOLE": "1"}
    kwargs: dict[str, object] = {
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen([sys.executable, "-m", "yaffo"], **kwargs)
    print(f"Yaffo is starting at http://{HOST}:{PORT}")


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args:
        if args[0] == "setup":
            run_setup(launch_fn=start_app_detached)
            return
        if args[0] == "uninstall":
            run_uninstall()
            return
        if args[0] == "install-shortcut":
            result = install_shortcut()
            print(f"Installed Yaffo {result.kind}: {result.path}")
            return
        if args[0] in ("-h", "--help"):
            print("Usage: yaffo [setup|uninstall|install-shortcut]")
            return
        print(f"Unknown command: {args[0]}", file=sys.stderr)
        raise SystemExit(2)

    start_app_detached()


if __name__ == "__main__":
    main()
