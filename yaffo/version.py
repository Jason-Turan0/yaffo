"""Build identity (version + timestamp), shown in Settings → System Information.

At build time `packaging/bump_version.py` bumps the committed `VERSION` file and
writes the generated, gitignored `yaffo/_build_info.py`. In a dev checkout that
generated module is absent, so we fall back to the `VERSION` file (live, at repo
root); in a pip/PyPI install neither is present, so we fall back to the installed
package metadata. Both report no build timestamp ("dev build").
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"


@dataclass(frozen=True)
class BuildInfo:
    version: str
    build_time: str | None  # human-readable UTC; None for a dev (unbuilt) run
    git_sha: str | None


def _dev_version() -> str:
    try:
        return _VERSION_FILE.read_text().strip()
    except OSError:
        pass
    try:
        return _pkg_version("yaffo")
    except PackageNotFoundError:
        return "0.0.0"


def get_build_info() -> BuildInfo:
    try:
        from yaffo import _build_info  # generated at build time
    except Exception:
        return BuildInfo(version=_dev_version(), build_time=None, git_sha=None)
    return BuildInfo(
        version=_build_info.VERSION,
        build_time=_build_info.BUILD_TIME,
        git_sha=getattr(_build_info, "GIT_SHA", None),
    )
