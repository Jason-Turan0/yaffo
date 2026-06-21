"""Bump the patch version and stamp build info. Run by build_dmg.sh before freezing.

Increments the committed `VERSION` file (e.g. 0.0.1 -> 0.0.2) and writes the
generated, gitignored `yaffo/_build_info.py` with the new version, a UTC build
timestamp, and the short git sha. The app reads these via yaffo.version.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
BUILD_INFO = ROOT / "yaffo" / "_build_info.py"


def _bump(version: str) -> str:
    major, minor, patch = (int(p) for p in (version.strip().split(".") + ["0", "0", "0"])[:3])
    return f"{major}.{minor}.{patch + 1}"


def main() -> None:
    current = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else "0.0.0"
    new = _bump(current)
    VERSION_FILE.write_text(new + "\n")

    build_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        sha = None

    BUILD_INFO.write_text(
        '"""Generated at build time by packaging/bump_version.py. Do not edit."""\n'
        f'VERSION = "{new}"\n'
        f'BUILD_TIME = "{build_time}"\n'
        f"GIT_SHA = {sha!r}\n"
    )
    print(f"bumped {current} -> {new}  ({build_time}, {sha})")


if __name__ == "__main__":
    main()
