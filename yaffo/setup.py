"""Interactive setup and cleanup flows for installed Yaffo copies."""
from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from yaffo.common import DB_PATH, FFMPEG_DIR, MODEL_CACHE_DIR, QUEUE_DB_PATH, ROOT_DIR
from yaffo.shortcuts import install_shortcut, uninstall_shortcut
from yaffo.utils.exiftool_path import EXIFTOOL_DIR_PREFIX

CONFIG_PATH = ROOT_DIR / "config.toml"
BACKGROUND_TASKS_LOG_FILE = ROOT_DIR / "background_tasks.log"
WEB_LOG_FILE = ROOT_DIR / "yaffo.log"

InputFn = Callable[[str], str]
PrintFn = Callable[[str], None]
LaunchFn = Callable[[], None]


def run_setup(
    *,
    input_fn: InputFn = input,
    print_fn: PrintFn = print,
    launch_fn: LaunchFn | None = None,
) -> None:
    print_fn(f"Yaffo data directory: {ROOT_DIR}")
    if _confirm("Install a desktop/app shortcut?", default=True, input_fn=input_fn, print_fn=print_fn):
        result = install_shortcut()
        print_fn(f"Installed Yaffo {result.kind}: {result.path}")

    print_fn("Installing databases...")
    _run_migrations()
    print_fn(f"Database ready: {DB_PATH}")

    print_fn("Downloading runtime assets...")
    _download_assets(print_fn)
    print_fn("Runtime assets ready.")

    if launch_fn is not None and _confirm("Launch Yaffo now?", default=True, input_fn=input_fn, print_fn=print_fn):
        launch_fn()


def run_uninstall(
    *,
    input_fn: InputFn = input,
    print_fn: PrintFn = print,
) -> None:
    shortcut = uninstall_shortcut()
    if shortcut.removed:
        print_fn(f"Removed Yaffo {shortcut.kind}: {shortcut.path}")
    else:
        print_fn(f"No Yaffo {shortcut.kind} found at: {shortcut.path}")

    removed_assets = _remove_existing(_asset_paths())
    _print_removed("Removed asset", removed_assets, print_fn)

    removed_logs = _remove_existing(_log_paths())
    _print_removed("Removed log", removed_logs, print_fn)

    if _confirm(
        f"Delete Yaffo user data in {ROOT_DIR}?",
        default=False,
        input_fn=input_fn,
        print_fn=print_fn,
    ):
        removed_data = _remove_existing(_user_data_paths())
        _print_removed("Removed user data", removed_data, print_fn)
        _remove_empty_root_dir(print_fn)
    else:
        print_fn("Kept Yaffo user data.")


def _download_assets(print_fn: PrintFn) -> None:
    from yaffo.download_assets import (
        download_clip,
        download_exiftool,
        download_ffmpeg,
        download_insightface,
    )

    for name, fn in (
        ("ExifTool", download_exiftool),
        ("InsightFace", download_insightface),
        ("CLIP", download_clip),
        ("ffmpeg", download_ffmpeg),
    ):
        print_fn(f"Preparing {name}...")
        fn()


def _run_migrations() -> None:
    from yaffo.scripts.db.migrate import run_migrations

    run_migrations()


def _asset_paths() -> list[Path]:
    paths = [MODEL_CACHE_DIR, FFMPEG_DIR]
    paths.extend(path for path in ROOT_DIR.glob(f"{EXIFTOOL_DIR_PREFIX}*") if path.is_dir())
    legacy_exiftool = ROOT_DIR / "Image-ExifTool"
    if legacy_exiftool.exists():
        paths.append(legacy_exiftool)
    return paths


def _log_paths() -> list[Path]:
    paths = [ROOT_DIR / "model_logs"]
    for base in (WEB_LOG_FILE, BACKGROUND_TASKS_LOG_FILE):
        paths.append(base)
        paths.extend(base.parent.glob(f"{base.name}.*"))
    return paths


def _user_data_paths() -> list[Path]:
    return [
        DB_PATH,
        QUEUE_DB_PATH,
        CONFIG_PATH,
        ROOT_DIR / "backups",
        ROOT_DIR / "cache",
        ROOT_DIR / "temp",
        ROOT_DIR / "trash",
        ROOT_DIR / "thumbnails",
    ]


def _remove_existing(paths: list[Path]) -> list[Path]:
    removed: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve(strict=False)
        if resolved in seen or not path.exists():
            continue
        seen.add(resolved)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(path)
    return removed


def _remove_empty_root_dir(print_fn: PrintFn) -> None:
    try:
        ROOT_DIR.rmdir()
    except OSError:
        return
    print_fn(f"Removed empty data directory: {ROOT_DIR}")


def _print_removed(prefix: str, paths: list[Path], print_fn: PrintFn) -> None:
    if not paths:
        print_fn(f"{prefix}: none found")
        return
    for path in paths:
        print_fn(f"{prefix}: {path}")


def _confirm(
    prompt: str,
    *,
    default: bool,
    input_fn: InputFn,
    print_fn: PrintFn,
) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            value = input_fn(prompt + suffix).strip().lower()
        except EOFError:
            print_fn("")
            return default
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print_fn("Please answer yes or no.")
