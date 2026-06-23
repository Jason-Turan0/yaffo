"""Optional user config, read once at startup from ``<data dir>/config.toml``.

TOML (not YAML/JSON) because it's the Python-conventional config format: the stdlib
``tomllib`` reads it with no dependency, it supports comments, and the project
already uses TOML (pyproject.toml). Edit the file and restart the app to apply.

On startup the file is migrated to the current template: new sections/keys (and their
documentation comments) are added while the user's set values are preserved. The
template is the source of truth for structure + comments + defaults, so we re-render
it with the user's overrides substituted in rather than round-tripping through a dict
(``tomllib`` can read but not write TOML, and would drop every comment).
"""
from __future__ import annotations

import os
import re
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
# Max size (MB) of each rolling log file before it rotates, and how many old files
# to keep.
max_file_mb = 5
backup_count = 3
# How many recent page/theme/automation generation runs to keep under model_logs/.
max_model_log_runs = 50

[database]
# SQLite durability vs. write speed (PRAGMA synchronous), paired with WAL.
#   NORMAL = fast; the last transaction can be lost on an OS/power/disk failure
#            (never corruption). The default — best for bulk photo indexing.
#   FULL   = fsync on every commit; survives OS/power/disk failures with no lost
#            writes, at a write-throughput cost.
# One of: NORMAL, FULL
synchronous = "NORMAL"

[tasks]
# Background worker processes: more = more parallel indexing, more CPU/RAM. Defaults
# to the CPU count when unset; a `--workers` CLI flag still overrides this.
# workers = 4
# Recycle (restart) a worker after this many tasks, to bound slow leaks.
recycle = 100

[ai]
# Caps per AI generation run (page/theme/automation builder), to bound cost.
# max_iterations: tool-use loop steps before the run stops.
# max_output_tokens: tokens per model call — shared with the thinking budget, so
#   setting it too low risks responses getting cut off mid-generation.
max_iterations = 25
max_output_tokens = 64000
"""


_SECTION_RE = re.compile(r"^\s*\[([A-Za-z0-9_.-]+)\]\s*$")
# A `key = value` line, optionally commented (e.g. the `# workers = 4` default).
_KEY_RE = re.compile(r"^(\s*)#?\s*([A-Za-z_][\w-]*)\s*=\s*(.*)$")


def _toml_value(value: Any) -> str:
    """Render a Python scalar as a TOML literal (covers the value types this config
    uses: str, int, float, bool)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render(user: dict[str, Any]) -> str:
    """Render _TEMPLATE (the canonical structure + comments + defaults), substituting
    any value the user has set so their overrides survive the migration. A commented
    default (e.g. `# workers = 4`) is activated when the user has set that key. Pure
    doc-comment lines and unknown user keys are left untouched / dropped."""
    section: str | None = None
    out: list[str] = []
    for line in _TEMPLATE.splitlines():
        sm = _SECTION_RE.match(line)
        if sm:
            section = sm.group(1)
            out.append(line)
            continue
        km = _KEY_RE.match(line)
        if km and section and section in user and km.group(2) in user[section]:
            indent, key = km.group(1), km.group(2)
            out.append(f"{indent}{key} = {_toml_value(user[section][key])}")
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def _atomic_write(text: str) -> None:
    """Write config.toml via a temp file + rename so concurrently-starting processes
    (flask, watcher, host, spawn workers all import this) never see a torn file."""
    tmp = CONFIG_PATH.with_suffix(".toml.tmp")
    tmp.write_text(text)
    os.replace(tmp, CONFIG_PATH)


def _load() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        try:
            _atomic_write(_TEMPLATE)  # seed a documented default on first run
        except OSError:
            pass
        return tomllib.loads(_TEMPLATE)

    try:
        existing = CONFIG_PATH.read_text()
        user = tomllib.loads(existing)
    except (OSError, tomllib.TOMLDecodeError):
        # Leave an unreadable/corrupt file alone (don't clobber something the user
        # could still fix); fall back to code defaults via get()/get_int().
        return {}

    # Migrate: re-render the template with the user's values, rewriting only when the
    # result actually differs (so a settled config doesn't get rewritten every launch).
    merged_text = _render(user)
    if merged_text != existing:
        try:
            _atomic_write(merged_text)
        except OSError:
            pass
    return tomllib.loads(merged_text)


_config = _load()


def get(section: str, key: str, default: Any = None) -> Any:
    """Read config[section][key], falling back to default if absent."""
    return _config.get(section, {}).get(key, default)


def get_int(section: str, key: str, default: int, minimum: int = 1) -> int:
    """Read an integer config value, falling back to `default` when absent, the wrong
    type, or below `minimum` — so a typo in config.toml can't feed a nonsensical value
    (e.g. 0 workers, a negative retention) into the system."""
    raw = get(section, key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default
