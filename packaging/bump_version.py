"""Bump or stamp the app version and build info.

By default this increments the patch version in the committed `VERSION` file
(e.g. 0.0.1 -> 0.0.2) and writes the generated, gitignored
`yaffo/_build_info.py` with the resulting version, a UTC build timestamp, and
the short git sha. The release workflow can choose the bump part or set an exact
version, then `build_dmg.sh` can stamp that same version without bumping again.
"""
from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
BUILD_INFO = ROOT / "yaffo" / "_build_info.py"


def _parse_version(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(p) for p in (version.strip().split(".") + ["0", "0", "0"])[:3])
    return major, minor, patch


def _bump(version: str, part: str) -> str:
    major, minor, patch = _parse_version(version)
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _stamp(version: str) -> None:
    _parse_version(version)

    build_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        sha = None

    BUILD_INFO.write_text(
        '"""Generated at build time by packaging/bump_version.py. Do not edit."""\n'
        f'VERSION = "{version}"\n'
        f'BUILD_TIME = "{build_time}"\n'
        f"GIT_SHA = {sha!r}\n"
    )
    print(f"stamped {version}  ({build_time}, {sha})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bump VERSION and stamp yaffo/_build_info.py")
    parser.add_argument(
        "--part",
        choices=("major", "minor", "patch"),
        default="patch",
        help="Version part to increment when not using --version or --stamp-only.",
    )
    parser.add_argument("--version", help="Set VERSION to this exact X.Y.Z value.")
    parser.add_argument(
        "--stamp-only",
        action="store_true",
        help="Do not change VERSION; only write yaffo/_build_info.py from the current VERSION.",
    )
    args = parser.parse_args()

    current = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else "0.0.0"
    if args.stamp_only:
        new = current
    elif args.version:
        new = args.version.strip()
        _parse_version(new)
        VERSION_FILE.write_text(new + "\n")
        print(f"set {current} -> {new}")
    else:
        new = _bump(current, args.part)
        VERSION_FILE.write_text(new + "\n")
        print(f"bumped {current} -> {new}")

    _stamp(new)


if __name__ == "__main__":
    main()
