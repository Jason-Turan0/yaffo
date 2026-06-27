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

    launcher.main()

    assert calls
    cmd, kwargs = calls[0]
    assert cmd == [launcher.sys.executable, "-m", "yaffo"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["start_new_session"] is True
    assert kwargs["env"]["YAFFO_LAUNCHED_FROM_CONSOLE"] == "1"
    assert "Yaffo is starting at http://127.0.0.1:5001" in capsys.readouterr().out
