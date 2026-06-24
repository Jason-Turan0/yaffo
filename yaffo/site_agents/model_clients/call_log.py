"""Per-call model-log writer, shared by every ModelClient.

Each agent run writes one timestamped sub-dir of per-call JSON dumps (request,
response, usage, cost) under `log_dir`; only the newest _MAX_LOG_RUNS runs are kept
so the dumps can't grow unbounded. Writing is gated on DEBUG so it's off in normal
operation. Factored out of the Anthropic client so the OpenAI-compatible client
reuses the same format and pruning instead of copy-pasting it.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from yaffo.common import ROOT_DIR
from yaffo.config import get_int as get_config_int
from yaffo.logging_config import get_logger

logger = get_logger(__name__)

# Count from config.toml ([logging] max_model_log_runs), default 50.
_MAX_LOG_RUNS = get_config_int("logging", "max_model_log_runs", 50)


def _jsonable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return str(obj)


class CallLogger:
    def __init__(self, log_dir: Optional[Path] = None):
        self.log_dir = Path(log_dir) if log_dir else (ROOT_DIR / "model_logs")
        self.task_start = datetime.now()
        self._call_count = 0
        self._prune_old_runs()

    def _prune_old_runs(self) -> None:
        """Keep only the newest _MAX_LOG_RUNS run sub-dirs under log_dir; delete the
        rest. Run dirs are named by start timestamp, so a lexical sort is chronological.
        Best-effort — a filesystem error here must never break a model call."""
        try:
            if not self.log_dir.exists():
                return
            run_dirs = sorted(d for d in self.log_dir.iterdir() if d.is_dir())
            for stale in run_dirs[:-_MAX_LOG_RUNS]:
                shutil.rmtree(stale, ignore_errors=True)
        except OSError:
            pass

    @property
    def enabled(self) -> bool:
        return logger.getEffectiveLevel() <= logging.DEBUG

    def write(
        self,
        *,
        model: str,
        timestamp: datetime,
        duration_ms: float,
        success: bool,
        request: Any,
        response: Any,
        cost: Optional[dict],
    ) -> None:
        if not self.enabled:
            return
        self._call_count += 1
        record = {
            "timestamp": timestamp.isoformat(timespec="seconds"),
            "call": self._call_count,
            "model": model,
            "duration_ms": round(duration_ms),
            "success": success,
            "request": request,
            "response": response,
            "cost": cost,
        }
        run_dir = self.log_dir / f"{self.task_start:%Y%m%d-%H%M%S}"
        file_path = run_dir / f"{self._call_count:03d}.json"
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            file_path.write_text(json.dumps(record, indent=2, default=_jsonable))
        except OSError:
            pass
