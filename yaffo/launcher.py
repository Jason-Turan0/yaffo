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

from yaffo.__main__ import HOST, PORT


def main() -> None:
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


if __name__ == "__main__":
    main()
