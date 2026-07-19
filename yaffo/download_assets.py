"""Download app-managed binary/model assets on demand.

Assets are stored under ``ASSET_DIR`` (``ROOT_DIR`` by default) so packaged and
source installs use the same runtime paths while containers can bake them into a
read-only image layer:

- ``ROOT_DIR/Image-ExifTool-<latest>``
- ``ROOT_DIR/models``
- ``ROOT_DIR/ffmpeg``
"""
from __future__ import annotations

import io
import os
import platform
import re
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from yaffo.common import ASSET_DIR, FFMPEG_DIR, MODEL_CACHE_DIR
from yaffo.logging_config import get_logger
from yaffo.utils.exiftool_path import EXIFTOOL_DIR_PREFIX, get_exiftool_path

logger = get_logger(__name__, "webapp")


SOURCEFORGE_EXIFTOOL_FILES_URL = "https://sourceforge.net/projects/exiftool/files/"
EXIFTOOL_FALLBACK_VERSION = "13.59"

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


def _fetch_text(url: str) -> str:
    return _fetch(url).decode("utf-8", errors="replace")


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _installed_exiftool_version() -> str | None:
    path = get_exiftool_path()
    if path is None:
        return None
    for parent in (path, *path.parents):
        if parent.name.startswith(EXIFTOOL_DIR_PREFIX):
            return parent.name.removeprefix(EXIFTOOL_DIR_PREFIX)
    return None


def _latest_exiftool_version(pattern: str) -> str:
    try:
        html = _fetch_text(SOURCEFORGE_EXIFTOOL_FILES_URL)
    except Exception as e:  # noqa: BLE001 - use the current known version when offline
        installed_version = _installed_exiftool_version()
        if installed_version is not None:
            logger.warning(
                "could not check latest ExifTool version; keeping installed %s: %s",
                installed_version,
                e,
            )
            return installed_version
        logger.warning(
            "could not check latest ExifTool version; falling back to %s: %s",
            EXIFTOOL_FALLBACK_VERSION,
            e,
        )
        return EXIFTOOL_FALLBACK_VERSION

    versions = sorted(set(re.findall(pattern, html)), key=_version_key, reverse=True)
    if not versions:
        raise RuntimeError("SourceForge ExifTool listing did not contain a matching package")
    return versions[0]


def _exiftool_source_url(version: str) -> str:
    return f"https://sourceforge.net/projects/exiftool/files/Image-ExifTool-{version}.tar.gz/download"


def _exiftool_windows_url(version: str, bits: str) -> str:
    return f"https://sourceforge.net/projects/exiftool/files/exiftool-{version}_{bits}.zip/download"


def _exiftool_dir(version: str) -> Path:
    return ASSET_DIR / f"{EXIFTOOL_DIR_PREFIX}{version}"


def _exiftool_src_dir(version: str) -> Path:
    return _exiftool_dir(version) / "src"


def _rm(path: Path) -> None:
    if path.is_dir():
        for root, _dirs, _files in os.walk(path):
            os.chmod(root, 0o700)
        shutil.rmtree(path)
    else:
        os.chmod(path.parent, 0o700)
        path.unlink()


def _prune_exiftool_source(version: str) -> None:
    exiftool_dir = _exiftool_dir(version)
    src_dir = _exiftool_src_dir(version)
    for child in exiftool_dir.iterdir():
        if child != src_dir:
            _rm(child)
    for child in src_dir.iterdir():
        if child.name not in ("exiftool", "lib"):
            _rm(child)


def _exiftool_windows_bits() -> str:
    return "64" if platform.machine().endswith("64") else "32"


def _exiftool_windows_dir(version: str) -> Path:
    return _exiftool_dir(version) / "bin" / f"exiftool-{version}_{_exiftool_windows_bits()}"


def _download_exiftool_windows() -> None:
    bits = _exiftool_windows_bits()
    version = _latest_exiftool_version(rf"exiftool-(\d+\.\d+)_{bits}\.zip")
    target_dir = _exiftool_windows_dir(version)
    target = target_dir / "exiftool.exe"
    if target.exists():
        logger.info("exiftool %s already present", version)
        return

    logger.info("downloading exiftool %s windows %s-bit package", version, bits)
    blob = _fetch(_exiftool_windows_url(version, bits))
    exiftool_dir = _exiftool_dir(version)
    tmp = exiftool_dir / "_exiftool_windows_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    if target_dir.exists():
        shutil.rmtree(target_dir)

    exiftool_dir.mkdir(parents=True, exist_ok=True)
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
    version = _latest_exiftool_version(r"Image-ExifTool-(\d+\.\d+)\.tar\.gz")
    src_dir = _exiftool_src_dir(version)
    if (src_dir / "exiftool").exists():
        logger.info("exiftool %s already present", version)
        _prune_exiftool_source(version)
        return

    logger.info("downloading exiftool %s", version)
    blob = _fetch(_exiftool_source_url(version))
    exiftool_dir = _exiftool_dir(version)
    tmp = exiftool_dir / "_exiftool_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)

    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        root = tar.getmembers()[0].name.split("/")[0]
        exiftool_dir.mkdir(parents=True, exist_ok=True)
        tar.extractall(tmp)

    if src_dir.exists():
        shutil.rmtree(src_dir)
    (tmp / root).rename(src_dir)
    shutil.rmtree(tmp, ignore_errors=True)
    (src_dir / "exiftool").chmod(0o755)
    _prune_exiftool_source(version)
    logger.info("exiftool installed at %s", src_dir)


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
