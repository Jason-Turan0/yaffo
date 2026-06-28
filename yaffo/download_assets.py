"""Download app-managed binary/model assets on demand.

Assets are stored under ``ROOT_DIR`` so packaged and source installs use the same
runtime paths:

- ``ROOT_DIR/Image-ExifTool-13.40``
- ``ROOT_DIR/models``
- ``ROOT_DIR/ffmpeg``
"""
from __future__ import annotations

import io
import os
import platform
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from yaffo.common import EXIFTOOL_DIR, FFMPEG_DIR, MODEL_CACHE_DIR
from yaffo.logging_config import get_logger

logger = get_logger(__name__, "webapp")

EXIFTOOL_VERSION = "13.40"
EXIFTOOL_SRC_DIR = EXIFTOOL_DIR / "src"
EXIFTOOL_SOURCE_URLS = [
    f"https://sourceforge.net/projects/exiftool/files/Image-ExifTool-{EXIFTOOL_VERSION}.tar.gz/download",
    f"https://github.com/exiftool/exiftool/archive/refs/tags/{EXIFTOOL_VERSION}.tar.gz",
]
EXIFTOOL_WINDOWS_URLS = {
    "32": f"https://sourceforge.net/projects/exiftool/files/exiftool-{EXIFTOOL_VERSION}_32.zip/download",
    "64": f"https://sourceforge.net/projects/exiftool/files/exiftool-{EXIFTOOL_VERSION}_64.zip/download",
}

INSIGHTFACE_DIR = MODEL_CACHE_DIR / "insightface" / "models" / "buffalo_l"
INSIGHTFACE_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
INSIGHTFACE_KEEP = {"det_10g.onnx", "w600k_r50.onnx", "genderage.onnx"}

CLIP_DIR = MODEL_CACHE_DIR / "clip" / "ViT-B-32__openai"
CLIP_BASE = "https://huggingface.co/immich-app/ViT-B-32__openai/resolve/main"
CLIP_FILES = ["visual/model.onnx", "textual/model.onnx"]

FFMPEG_RELEASE = "b6.1.1"
FFMPEG_BASE = f"https://github.com/eugeneware/ffmpeg-static/releases/download/{FFMPEG_RELEASE}"
FFMPEG_ASSETS = {
    ("darwin", "arm64"): "darwin-arm64",
    ("darwin", "x86_64"): "darwin-x64",
    ("windows", "amd64"): "win32-x64",
    ("linux", "x86_64"): "linux-x64",
    ("linux", "aarch64"): "linux-arm64",
}


def _fetch(url: str) -> bytes:
    logger.info("downloading %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "yaffo"})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def _fetch_first(urls: list[str]) -> bytes:
    last: Exception | None = None
    for url in urls:
        try:
            return _fetch(url)
        except Exception as e:  # noqa: BLE001 - try the next mirror
            last = e
            logger.warning("asset source failed for %s: %s", url, e)
    raise RuntimeError(f"all sources failed for {urls}") from last


def _rm(path: Path) -> None:
    if path.is_dir():
        for root, _dirs, _files in os.walk(path):
            os.chmod(root, 0o700)
        shutil.rmtree(path)
    else:
        os.chmod(path.parent, 0o700)
        path.unlink()


def _prune_exiftool() -> None:
    for child in EXIFTOOL_DIR.iterdir():
        if child != EXIFTOOL_SRC_DIR:
            _rm(child)
    for child in EXIFTOOL_SRC_DIR.iterdir():
        if child.name not in ("exiftool", "lib"):
            _rm(child)


def _exiftool_windows_bits() -> str:
    return "64" if platform.machine().endswith("64") else "32"


def _exiftool_windows_dir() -> Path:
    return EXIFTOOL_DIR / "bin" / f"exiftool-{EXIFTOOL_VERSION}_{_exiftool_windows_bits()}"


def _download_exiftool_windows() -> None:
    target_dir = _exiftool_windows_dir()
    target = target_dir / "exiftool.exe"
    if target.exists():
        logger.info("exiftool already present")
        return

    bits = _exiftool_windows_bits()
    logger.info("downloading exiftool windows %s-bit package", bits)
    blob = _fetch(EXIFTOOL_WINDOWS_URLS[bits])
    tmp = EXIFTOOL_DIR / "_exiftool_windows_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    if target_dir.exists():
        shutil.rmtree(target_dir)

    EXIFTOOL_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        zf.extractall(tmp)

    top_dirs = [child for child in tmp.iterdir() if child.is_dir()]
    top_files = [child for child in tmp.iterdir() if child.is_file()]
    payload_root = top_dirs[0] if len(top_dirs) == 1 and not top_files else tmp

    target_dir.mkdir(parents=True, exist_ok=True)
    for child in payload_root.iterdir():
        child_target = target_dir / child.name
        if child.is_dir():
            shutil.move(str(child), child_target)
        else:
            shutil.move(str(child), child_target)

    exe_candidates = sorted(target_dir.glob("*.exe"))
    if not exe_candidates:
        raise RuntimeError("exiftool windows package did not contain an executable")
    if exe_candidates[0] != target:
        exe_candidates[0].rename(target)
    shutil.rmtree(tmp, ignore_errors=True)
    logger.info("exiftool installed at %s", target_dir)


def _download_exiftool_source() -> None:
    if (EXIFTOOL_SRC_DIR / "exiftool").exists():
        logger.info("exiftool already present")
        _prune_exiftool()
        return

    logger.info("downloading exiftool")
    blob = _fetch_first(EXIFTOOL_SOURCE_URLS)
    tmp = EXIFTOOL_DIR / "_exiftool_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)

    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        root = tar.getmembers()[0].name.split("/")[0]
        EXIFTOOL_DIR.mkdir(parents=True, exist_ok=True)
        tar.extractall(tmp)

    if EXIFTOOL_SRC_DIR.exists():
        shutil.rmtree(EXIFTOOL_SRC_DIR)
    (tmp / root).rename(EXIFTOOL_SRC_DIR)
    shutil.rmtree(tmp, ignore_errors=True)
    (EXIFTOOL_SRC_DIR / "exiftool").chmod(0o755)
    _prune_exiftool()
    logger.info("exiftool installed at %s", EXIFTOOL_SRC_DIR)


def download_exiftool() -> None:
    if platform.system().lower() == "windows":
        _download_exiftool_windows()
    else:
        _download_exiftool_source()


def _prune_insightface() -> None:
    for onnx in INSIGHTFACE_DIR.glob("*.onnx"):
        if onnx.name not in INSIGHTFACE_KEEP:
            onnx.unlink()
            logger.info("pruned unused InsightFace model %s", onnx.name)


def download_insightface() -> None:
    if any(INSIGHTFACE_DIR.glob("*.onnx")):
        logger.info("insightface already present")
        _prune_insightface()
        return

    logger.info("downloading InsightFace buffalo_l")
    blob = _fetch(INSIGHTFACE_URL)
    INSIGHTFACE_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for name in zf.namelist():
            if name.endswith("/") or Path(name).name not in INSIGHTFACE_KEEP:
                continue
            target = INSIGHTFACE_DIR / Path(name).name
            target.write_bytes(zf.read(name))
    logger.info("insightface installed at %s", INSIGHTFACE_DIR)


def download_clip() -> None:
    for rel in CLIP_FILES:
        target = CLIP_DIR / rel
        if target.is_file():
            logger.info("clip %s already present", rel)
            continue
        logger.info("downloading CLIP %s", rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_fetch(f"{CLIP_BASE}/{rel}"))
        logger.info("clip %s installed at %s", rel, target)


def download_ffmpeg() -> None:
    is_windows = platform.system().lower() == "windows"
    target = FFMPEG_DIR / ("ffmpeg.exe" if is_windows else "ffmpeg")
    if target.exists():
        logger.info("ffmpeg already present")
        return

    key = (platform.system().lower(), platform.machine().lower())
    slug = FFMPEG_ASSETS.get(key)
    if slug is None:
        logger.warning("no ffmpeg static build mapped for %s; skipping", key)
        return

    logger.info("downloading ffmpeg %s", slug)
    FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_fetch(f"{FFMPEG_BASE}/ffmpeg-{slug}"))
    target.chmod(0o755)
    (FFMPEG_DIR / "ffmpeg.LICENSE").write_bytes(_fetch(f"{FFMPEG_BASE}/{slug}.LICENSE"))
    logger.info("ffmpeg installed at %s", target)


def main() -> int:
    download_exiftool()
    download_insightface()
    download_clip()
    download_ffmpeg()
    logger.info("all app assets present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
