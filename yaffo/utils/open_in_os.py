"""Open a file or folder with the operating system, as if double-clicked, or show a
file in its folder (Finder / Explorer). Used by the media page's open buttons and
by the assistant's open links."""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path


def open_in_os(path: Path, *, reveal: bool = False) -> None:
    """Open `path` with its default app (a folder opens in the file manager). With
    `reveal`, show a file selected in its folder instead of opening it. Raises
    OSError or subprocess.SubprocessError when the OS can't."""
    system = platform.system()
    if system == "Darwin":
        subprocess.run(["open", "-R", str(path)] if reveal else ["open", str(path)], check=True)
    elif system == "Windows":
        if reveal:
            # explorer exits non-zero even on success, so its status isn't checked.
            subprocess.run(["explorer", f"/select,{path}"], check=False)
        else:
            os.startfile(path)  # type: ignore[attr-defined]  # Windows only
    else:
        # xdg-open has no "select this file"; open the folder that holds it.
        subprocess.run(["xdg-open", str(path.parent if reveal else path)], check=True)
