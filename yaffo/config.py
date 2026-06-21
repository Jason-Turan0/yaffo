"""Optional user config, read once at startup from ``<data dir>/config.toml``.

TOML (not YAML/JSON) because it's the Python-conventional config format: the stdlib
``tomllib`` reads it with no dependency, it supports comments, and the project
already uses TOML (pyproject.toml). Edit the file and restart the app to apply.
"""
from __future__ import annotations

import tomllib
from typing import Any

from yaffo.common import ROOT_DIR

CONFIG_PATH = ROOT_DIR / "config.toml"

_TEMPLATE = """\
# Yaffo configuration. Edit a value and restart the app to apply it.

[logging]
# Verbosity of the log files (yaffo.log, background_tasks.log).
# One of: DEBUG, INFO, WARNING, ERROR
level = "INFO"
"""


def _load() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        try:
            CONFIG_PATH.write_text(_TEMPLATE)  # seed a documented default on first run
        except OSError:
            pass
    try:
        with open(CONFIG_PATH, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


_config = _load()


def get(section: str, key: str, default: Any = None) -> Any:
    """Read config[section][key], falling back to default if absent."""
    return _config.get(section, {}).get(key, default)
