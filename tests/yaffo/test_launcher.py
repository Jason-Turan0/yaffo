import subprocess

import pytest

from yaffo import launcher

pytestmark = pytest.mark.unit


def test_launcher_starts_module_detached(monkeypatch, capsys):
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launcher.sys, "platform", "darwin")

    launcher.main([])

    assert calls
    cmd, kwargs = calls[0]
    assert cmd == [launcher.sys.executable, "-m", "yaffo"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["start_new_session"] is True
    assert kwargs["env"]["YAFFO_LAUNCHED_FROM_CONSOLE"] == "1"
    assert "Yaffo is starting at http://127.0.0.1:5001" in capsys.readouterr().out


def test_launcher_installs_shortcut(monkeypatch, tmp_path, capsys):
    class Result:
        kind = "desktop entry"
        path = tmp_path / "yaffo.desktop"

    monkeypatch.setattr(launcher, "install_shortcut", lambda: Result())

    launcher.main(["install-shortcut"])

    assert f"Installed Yaffo desktop entry: {Result.path}" in capsys.readouterr().out


def test_launcher_runs_setup(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "run_setup", lambda **kwargs: calls.append(kwargs))

    launcher.main(["setup"])

    assert calls
    assert calls[0]["launch_fn"] is launcher.start_app_detached


def test_launcher_runs_uninstall(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "run_uninstall", lambda: calls.append(True))

    launcher.main(["uninstall"])

    assert calls == [True]


def test_launcher_unknown_command_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        launcher.main(["nope"])

    assert exc.value.code == 2
    assert "Unknown command: nope" in capsys.readouterr().err
