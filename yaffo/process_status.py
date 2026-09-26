"""Small, atomic process-status records for local diagnostics."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from yaffo.common import ROOT_DIR

ROLES = frozenset({"web", "watcher"})
STALE_SECONDS = 60.0


def write_status(role: str, started_at: float, *, healthy: bool = True,
                 data_dir: Path = ROOT_DIR) -> None:
    if role not in ROLES:
        raise ValueError(f"Unknown process role: {role}")
    path = data_dir / f"{role}_status.json"
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps({"pid": os.getpid(), "started_at": started_at,
                                         "beat_at": time.time(), "healthy": healthy}))
        temporary.replace(path)
    except OSError:
        # Diagnostics must not stop the process being monitored.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def read_status(role: str, *, data_dir: Path = ROOT_DIR) -> Optional[dict]:
    if role not in ROLES:
        raise ValueError(f"Unknown process role: {role}")
    try:
        with (data_dir / f"{role}_status.json").open() as handle:
            value = json.loads(handle.read(4096))
        if not isinstance(value, dict) or not all(
            isinstance(value.get(key), (int, float)) for key in ("pid", "started_at", "beat_at")
        ) or not isinstance(value.get("healthy"), bool):
            return None
        return value
    except (OSError, ValueError):
        return None


def start_web_status() -> None:
    started_at = time.time()
    write_status("web", started_at)

    def heartbeat() -> None:
        while True:
            time.sleep(5)
            write_status("web", started_at)

    threading.Thread(target=heartbeat, daemon=True, name="web-heartbeat").start()
