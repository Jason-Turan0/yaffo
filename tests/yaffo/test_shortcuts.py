from pathlib import Path

import pytest

from yaffo import shortcuts

pytestmark = pytest.mark.unit


def test_linux_shortcut_writes_desktop_file(monkeypatch, tmp_path):
    resources = tmp_path / "resources"
    icon = resources / "branding" / "menubar.png"
    icon.parent.mkdir(parents=True)
    icon.write_text("png")
    monkeypatch.setattr(shortcuts, "RESOURCES_DIR", resources)
    monkeypatch.setattr(shortcuts.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(shortcuts.sys, "executable", "/venv/bin/python")

    result = shortcuts._install_linux_desktop_file()

    assert result.kind == "desktop entry"
    assert result.path == tmp_path / ".local" / "share" / "applications" / "yaffo.desktop"
    body = result.path.read_text()
    assert "Name=Yaffo" in body
    assert "Exec=/venv/bin/python -m yaffo.launcher" in body
    assert f"Icon={icon}" in body


def test_macos_shortcut_writes_app_bundle(monkeypatch, tmp_path):
    resources = tmp_path / "resources"
    resources.mkdir()
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.setattr(shortcuts, "RESOURCES_DIR", resources)
    monkeypatch.setattr(shortcuts.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(shortcuts.sys, "executable", "/venv/bin/python")

    result = shortcuts._install_macos_app()

    assert result.kind == "macOS app"
    assert result.path == tmp_path / "Desktop" / "Yaffo.app"
    executable = result.path / "Contents" / "MacOS" / "Yaffo"
    assert executable.exists()
    assert "exec /venv/bin/python -m yaffo.launcher" in executable.read_text()
    assert (result.path / "Contents" / "Info.plist").exists()


def test_windows_shortcut_uses_powershell(monkeypatch, tmp_path):
    calls = []
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.setattr(shortcuts.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(shortcuts.sys, "executable", r"C:\Python313\python.exe")
    monkeypatch.setattr(shortcuts.subprocess, "run", lambda *a, **k: calls.append((a, k)))

    result = shortcuts._install_windows_shortcut()

    assert result.kind == "Windows shortcut"
    assert result.path == tmp_path / "Desktop" / "Yaffo.lnk"
    args, kwargs = calls[0]
    command = args[0]
    script = command[-1]
    assert command[:4] == ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass"]
    assert "$s.TargetPath = 'C:\\Python313\\python.exe'" in script
    assert "$s.Arguments = '-m yaffo.launcher'" in script
    assert kwargs["check"] is True


def test_install_shortcut_dispatches_by_platform(monkeypatch):
    monkeypatch.setattr(shortcuts.sys, "platform", "linux")
    monkeypatch.setattr(
        shortcuts,
        "_install_linux_desktop_file",
        lambda: shortcuts.ShortcutResult(Path("/tmp/yaffo.desktop"), "desktop entry"),
    )

    result = shortcuts.install_shortcut()

    assert result.path == Path("/tmp/yaffo.desktop")


def test_uninstall_shortcut_removes_linux_desktop_file(monkeypatch, tmp_path):
    monkeypatch.setattr(shortcuts.sys, "platform", "linux")
    monkeypatch.setattr(shortcuts.Path, "home", lambda: tmp_path)
    shortcut = tmp_path / ".local" / "share" / "applications" / "yaffo.desktop"
    shortcut.parent.mkdir(parents=True)
    shortcut.write_text("launcher")

    result = shortcuts.uninstall_shortcut()

    assert result.removed is True
    assert result.path == shortcut
    assert not shortcut.exists()


def test_uninstall_shortcut_reports_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(shortcuts.sys, "platform", "darwin")
    monkeypatch.setattr(shortcuts.Path, "home", lambda: tmp_path)

    result = shortcuts.uninstall_shortcut()

    assert result.removed is False
    assert result.path == tmp_path / "Desktop" / "Yaffo.app"
