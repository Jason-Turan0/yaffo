"""Install per-user launcher shortcuts for PyPI/pipx installs."""
from __future__ import annotations

import os
import plistlib
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from yaffo.common import RESOURCES_DIR


@dataclass(frozen=True)
class ShortcutResult:
    path: Path
    kind: str


def install_shortcut() -> ShortcutResult:
    if sys.platform == "darwin":
        return _install_macos_app()
    if sys.platform == "win32":
        return _install_windows_shortcut()
    return _install_linux_desktop_file()


def _launcher_command() -> list[str]:
    return [sys.executable, "-m", "yaffo.launcher"]


def _desktop_dir() -> Path:
    path = os.environ.get("XDG_DESKTOP_DIR")
    if path:
        return Path(path).expanduser()
    return Path.home() / "Desktop"


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _install_macos_app() -> ShortcutResult:
    app_path = _desktop_dir() / "Yaffo.app"
    contents = app_path / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    command = _launcher_command()
    _write_executable(
        macos / "Yaffo",
        "#!/bin/sh\n"
        f"exec {shlex_join(command)}\n",
    )

    icon_file = _macos_icon(resources)
    plist = {
        "CFBundleName": "Yaffo",
        "CFBundleDisplayName": "Yaffo",
        "CFBundleIdentifier": "app.yaffo.launcher",
        "CFBundleExecutable": "Yaffo",
        "CFBundlePackageType": "APPL",
    }
    if icon_file is not None:
        plist["CFBundleIconFile"] = icon_file.name
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump(plist, f)
    return ShortcutResult(path=app_path, kind="macOS app")


def _macos_icon(resources: Path) -> Path | None:
    png = RESOURCES_DIR / "branding" / "menubar.png"
    if not png.exists():
        return None
    icns = resources / "Yaffo.icns"
    iconset = resources / "Yaffo.iconset"
    if shutil.which("sips") and shutil.which("iconutil"):
        if iconset.exists():
            shutil.rmtree(iconset)
        iconset.mkdir()
        sizes = (16, 32, 64, 128, 256, 512)
        for size in sizes:
            out = iconset / f"icon_{size}x{size}.png"
            subprocess.run(
                ["sips", "-z", str(size), str(size), str(png), "--out", str(out)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if size <= 256:
                out_2x = iconset / f"icon_{size}x{size}@2x.png"
                subprocess.run(
                    ["sips", "-z", str(size * 2), str(size * 2), str(png), "--out", str(out_2x)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(iconset, ignore_errors=True)
        if icns.exists():
            return icns
    shutil.copy2(png, resources / png.name)
    return None


def _install_windows_shortcut() -> ShortcutResult:
    desktop = _desktop_dir()
    desktop.mkdir(parents=True, exist_ok=True)
    shortcut = desktop / "Yaffo.lnk"
    command = _launcher_command()
    arguments = subprocess.list2cmdline(command[1:])
    script = (
        "$ws = New-Object -ComObject WScript.Shell\n"
        f"$s = $ws.CreateShortcut({_ps_literal(str(shortcut))})\n"
        f"$s.TargetPath = {_ps_literal(command[0])}\n"
        f"$s.Arguments = {_ps_literal(arguments)}\n"
        f"$s.WorkingDirectory = {_ps_literal(str(Path.home()))}\n"
        f"$s.IconLocation = {_ps_literal(sys.executable)}\n"
        "$s.Save()\n"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
    )
    return ShortcutResult(path=shortcut, kind="Windows shortcut")


def _install_linux_desktop_file() -> ShortcutResult:
    applications = Path.home() / ".local" / "share" / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    desktop_file = applications / "yaffo.desktop"
    icon = RESOURCES_DIR / "branding" / "menubar.png"
    icon_line = f"Icon={icon}\n" if icon.exists() else ""
    command = shlex_join(_launcher_command())
    _write_executable(
        desktop_file,
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Yaffo\n"
        "Comment=Photo organizer\n"
        f"Exec={command}\n"
        f"{icon_line}"
        "Terminal=false\n"
        "Categories=Graphics;Photography;\n",
    )
    return ShortcutResult(path=desktop_file, kind="desktop entry")


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def shlex_join(command: list[str]) -> str:
    import shlex

    return " ".join(shlex.quote(part) for part in command)
