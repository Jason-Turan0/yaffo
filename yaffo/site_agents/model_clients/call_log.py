"""Per-call model-log writer, shared by every ModelClient.

Each run records bounded metadata summaries even without DEBUG logging. Full
request/response dumps are DEBUG-only for builders; assistant conversations keep
them until deletion, without newest-N pruning. Run names include a unique suffix
so concurrent calls cannot overwrite another run.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from yaffo.common import ROOT_DIR
from yaffo.config import get_int as get_config_int
from yaffo.logging_config import get_logger
from yaffo.site_agents.model_clients.providers import provider_for_model

logger = get_logger(__name__)

# Count from config.toml ([logging] max_model_log_runs), default 50.
_MAX_LOG_RUNS = get_config_int("logging", "max_model_log_runs", 50)


def _jsonable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return str(obj)


class CallLogger:
    def __init__(self, log_dir: Optional[Path] = None, *, persistent: bool = False, feature: str = "unknown"):
        self.log_dir = Path(log_dir) if log_dir else (ROOT_DIR / "model_logs")
        self.task_start = datetime.now()
        self._call_count = 0
        self.persistent = persistent
        self.feature = feature
        self._run_name = f"{self.task_start:%Y%m%d-%H%M%S-%f}-{uuid4().hex[:8]}"
        self._summaries: list[dict] = []
        if persistent:
            try:
                self.log_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        else:
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
        return self.persistent or logger.getEffectiveLevel() <= logging.DEBUG

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
        if self.persistent and not self.log_dir.is_dir():
            return  # The conversation was deleted while this request was in flight.
        self._call_count += 1
        provider = provider_for_model(model)
        record = {
            "feature": self.feature,
            "provider": provider.id if provider else "unknown",
            "timestamp": timestamp.isoformat(timespec="seconds"),
            "call": self._call_count,
            "model": model,
            "duration_ms": round(duration_ms),
            "success": success,
            "request": request,
            "response": response,
            "cost": cost,
        }
        run_dir = self.log_dir / self._run_name
        self._summaries.append({key: record[key] for key in
                                ("timestamp", "call", "feature", "provider", "model", "duration_ms", "success", "cost")})
        file_path = run_dir / f"{self._call_count:03d}.json"
        try:
            run_dir.mkdir(parents=not self.persistent, exist_ok=True)
            summary = run_dir / "summary.json"
            temporary = run_dir / "summary.tmp"
            temporary.write_text(json.dumps(self._summaries[-100:], default=_jsonable))
            temporary.replace(summary)
            if self.enabled:
                file_path.write_text(json.dumps(record, indent=2, default=_jsonable))
        except OSError:
            pass
