"""Opening a file or folder with the OS, per platform."""
from pathlib import Path

import pytest

from yaffo.utils import open_in_os as module

pytestmark = pytest.mark.unit


@pytest.fixture
def runs(monkeypatch):
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda args, check: calls.append((args, check)))
    return calls


@pytest.mark.parametrize("system,reveal,expected", [
    ("Darwin", False, (["open", "/p/a.jpg"], True)),
    ("Darwin", True, (["open", "-R", "/p/a.jpg"], True)),
    ("Windows", True, (["explorer", "/select,/p/a.jpg"], False)),
    ("Linux", False, (["xdg-open", "/p/a.jpg"], True)),
    ("Linux", True, (["xdg-open", "/p"], True)),
])
def test_commands(monkeypatch, runs, system, reveal, expected):
    monkeypatch.setattr(module.platform, "system", lambda: system)
    module.open_in_os(Path("/p/a.jpg"), reveal=reveal)
    assert runs == [expected]


def test_windows_open_uses_startfile(monkeypatch, runs):
    started = []
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(module.os, "startfile", started.append, raising=False)
    module.open_in_os(Path("/p/a.jpg"))
    assert started == [Path("/p/a.jpg")] and runs == []
