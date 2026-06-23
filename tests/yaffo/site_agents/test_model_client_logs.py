"""Model-log retention: each agent run dumps a timestamped sub-dir of per-call JSON,
and the client prunes to the newest _MAX_LOG_RUNS on construction so model_logs can't
grow unbounded."""
from pathlib import Path

import pytest

from yaffo.site_agents.model_clients.model_client import AnthropicModelClient, _MAX_LOG_RUNS

pytestmark = pytest.mark.unit


def _make_run_dirs(base: Path, n: int) -> None:
    # Timestamp-style names so a lexical sort is chronological (i ascending = newer).
    for i in range(n):
        d = base / f"2026{i:04d}-000000"
        d.mkdir(parents=True)
        (d / "001.json").write_text("{}")


def _client(log_dir: Path) -> AnthropicModelClient:
    return AnthropicModelClient(
        model="claude-haiku-4-5-20251001",
        system_prompt="x",
        log_dir=log_dir,
        api_key="test",  # stored, never used (no network at construction)
    )


def test_prune_keeps_only_newest_runs(tmp_path):
    log_dir = tmp_path / "model_logs"
    _make_run_dirs(log_dir, _MAX_LOG_RUNS + 5)

    _client(log_dir)  # prunes on construction

    remaining = sorted(d.name for d in log_dir.iterdir() if d.is_dir())
    assert len(remaining) == _MAX_LOG_RUNS
    # the 5 oldest were dropped; the newest survive
    assert remaining[0] == "20260005-000000"
    assert remaining[-1] == f"2026{_MAX_LOG_RUNS + 4:04d}-000000"


def test_prune_noop_under_cap(tmp_path):
    log_dir = tmp_path / "model_logs"
    _make_run_dirs(log_dir, 3)
    _client(log_dir)
    assert len([d for d in log_dir.iterdir() if d.is_dir()]) == 3


def test_prune_handles_missing_dir(tmp_path):
    # No log dir yet (first ever run) must not raise.
    _client(tmp_path / "does_not_exist")
