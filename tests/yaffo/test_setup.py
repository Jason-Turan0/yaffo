from pathlib import Path

import pytest

from yaffo import setup
from yaffo.shortcuts import ShortcutRemovalResult

pytestmark = pytest.mark.unit


def _answers(*values):
    answers = list(values)

    def input_fn(_prompt):
        if not answers:
            raise AssertionError("unexpected prompt")
        return answers.pop(0)

    return input_fn


def test_run_setup_installs_shortcut_migrates_downloads_and_launches(monkeypatch, tmp_path):
    calls = []

    class Shortcut:
        kind = "desktop entry"
        path = tmp_path / "yaffo.desktop"

    monkeypatch.setattr(setup, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(setup, "DB_PATH", tmp_path / "yaffo.db")
    monkeypatch.setattr(setup, "install_shortcut", lambda: calls.append("shortcut") or Shortcut())
    monkeypatch.setattr(setup, "_run_migrations", lambda: calls.append("migrate"))
    monkeypatch.setattr(setup, "_download_assets", lambda _print_fn: calls.append("assets"))

    setup.run_setup(
        input_fn=_answers("y", "y"),
        print_fn=lambda _message: None,
        launch_fn=lambda: calls.append("launch"),
    )

    assert calls == ["shortcut", "migrate", "assets", "launch"]


def test_run_uninstall_removes_assets_logs_and_keeps_user_data_by_default(monkeypatch, tmp_path):
    assets = tmp_path / "models"
    logs = tmp_path / "yaffo.log"
    db = tmp_path / "yaffo.db"
    assets.mkdir()
    logs.write_text("log")
    db.write_text("db")

    monkeypatch.setattr(setup, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(setup, "_asset_paths", lambda: [assets])
    monkeypatch.setattr(setup, "_log_paths", lambda: [logs])
    monkeypatch.setattr(setup, "_user_data_paths", lambda: [db])
    monkeypatch.setattr(
        setup,
        "uninstall_shortcut",
        lambda: ShortcutRemovalResult(Path("/tmp/yaffo.desktop"), "desktop entry", False),
    )

    setup.run_uninstall(input_fn=_answers(""), print_fn=lambda _message: None)

    assert not assets.exists()
    assert not logs.exists()
    assert db.exists()
    assert tmp_path.exists()


def test_run_uninstall_deletes_user_data_after_confirmation(monkeypatch, tmp_path):
    db = tmp_path / "yaffo.db"
    db.write_text("db")

    monkeypatch.setattr(setup, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(setup, "_asset_paths", lambda: [])
    monkeypatch.setattr(setup, "_log_paths", lambda: [])
    monkeypatch.setattr(setup, "_user_data_paths", lambda: [db])
    monkeypatch.setattr(
        setup,
        "uninstall_shortcut",
        lambda: ShortcutRemovalResult(Path("/tmp/yaffo.desktop"), "desktop entry", False),
    )

    setup.run_uninstall(input_fn=_answers("y"), print_fn=lambda _message: None)

    assert not db.exists()
    assert not tmp_path.exists()
