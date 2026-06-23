"""Download the binary assets the app bundles but that aren't in source control:
ExifTool, the InsightFace `buffalo_l` face models, the CLIP ONNX encoders, and the
ffmpeg binary (video poster frames).

Run before the PyInstaller build (the build script does this for you). Idempotent:
each asset is skipped if it's already in place. Targets match where the loaders
look at runtime (yaffo.utils.exiftool_path / face_analysis / image_classifier /
ffmpeg_path).
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

REPO_ROOT = Path(__file__).resolve().parents[1]
RESOURCES = REPO_ROOT / "resources"

EXIFTOOL_VERSION = "13.40"
EXIFTOOL_DIR = RESOURCES / f"Image-ExifTool-{EXIFTOOL_VERSION}" / "src"
EXIFTOOL_URLS = [
    f"https://exiftool.org/Image-ExifTool-{EXIFTOOL_VERSION}.tar.gz",
    f"https://exiftool.org/older/Image-ExifTool-{EXIFTOOL_VERSION}.tar.gz",
]

# InsightFace expects <root>/models/buffalo_l/*.onnx (root = resources/models/insightface).
INSIGHTFACE_DIR = RESOURCES / "models" / "insightface" / "models" / "buffalo_l"
INSIGHTFACE_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
# Only the models for the modules we load (face_analysis: detection / recognition /
# genderage). The two landmark models in the pack are unused dead weight (~142MB):
#   1k3d68.onnx (3D landmarks), 2d106det.onnx (2D landmarks).
INSIGHTFACE_KEEP = {"det_10g.onnx", "w600k_r50.onnx", "genderage.onnx"}

# CLIP encoders (immich's ONNX export); paths mirror image_classifier._FILES.
CLIP_DIR = RESOURCES / "models" / "clip" / "ViT-B-32__openai"
CLIP_BASE = "https://huggingface.co/immich-app/ViT-B-32__openai/resolve/main"
CLIP_FILES = ["visual/model.onnx", "textual/model.onnx"]

# ffmpeg static binary for poster-frame extraction (yaffo.utils.ffmpeg_path looks
# here). One binary for the current platform/arch — the build runs on the machine
# it targets. These are GPL builds; invoked only as a subprocess (mere aggregation),
# so the copyleft doesn't reach the app, but the license ships beside it (see
# THIRD_PARTY_LICENSES.txt) and the source is at github.com/eugeneware/ffmpeg-static.
FFMPEG_DIR = RESOURCES / "ffmpeg"
FFMPEG_RELEASE = "b6.1.1"
FFMPEG_BASE = f"https://github.com/eugeneware/ffmpeg-static/releases/download/{FFMPEG_RELEASE}"
# (system, machine) -> ffmpeg-static asset slug for that platform/arch.
FFMPEG_ASSETS = {
    ("darwin", "arm64"): "darwin-arm64",
    ("darwin", "x86_64"): "darwin-x64",
    ("windows", "amd64"): "win32-x64",
    ("linux", "x86_64"): "linux-x64",
    ("linux", "aarch64"): "linux-arm64",
}


def _fetch(url: str) -> bytes:
    print(f"  GET {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "yaffo-build"})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def _fetch_first(urls: list[str]) -> bytes:
    last: Exception | None = None
    for url in urls:
        try:
            return _fetch(url)
        except Exception as e:  # noqa: BLE001 - try the next mirror
            last = e
            print(f"  (failed: {e})")
    raise RuntimeError(f"all sources failed for {urls}") from last


def _rm(path: Path) -> None:
    """Remove a file or tree, forcing write permission first (the bundled Windows
    exiftool ships read-only dirs, and removing a file needs a writable parent)."""
    if path.is_dir():
        for root, _dirs, _files in os.walk(path):
            os.chmod(root, 0o700)
        shutil.rmtree(path)
    else:
        os.chmod(path.parent, 0o700)
        path.unlink()


def _prune_exiftool() -> None:
    """Keep only what's needed to run exiftool: the `exiftool` script and `lib/`.
    Drops the test suite (which ships Mach-O/PE fixtures that break PyInstaller's
    binary scan), docs, and any sibling dirs (e.g. the Windows `bin/`). Idempotent."""
    base = EXIFTOOL_DIR.parent  # Image-ExifTool-<ver>
    for child in base.iterdir():
        if child != EXIFTOOL_DIR:
            _rm(child)
    for child in EXIFTOOL_DIR.iterdir():
        if child.name not in ("exiftool", "lib"):
            _rm(child)


def download_exiftool() -> None:
    if (EXIFTOOL_DIR / "exiftool").exists():
        print("exiftool: already present")
        _prune_exiftool()
        return
    print("exiftool: downloading")
    blob = _fetch_first(EXIFTOOL_URLS)
    tmp = EXIFTOOL_DIR.parent / "_exiftool_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        root = tar.getmembers()[0].name.split("/")[0]  # e.g. Image-ExifTool-13.40
        EXIFTOOL_DIR.parent.mkdir(parents=True, exist_ok=True)
        tar.extractall(tmp)
    if EXIFTOOL_DIR.exists():
        shutil.rmtree(EXIFTOOL_DIR)
    (tmp / root).rename(EXIFTOOL_DIR)
    shutil.rmtree(tmp, ignore_errors=True)
    (EXIFTOOL_DIR / "exiftool").chmod(0o755)
    _prune_exiftool()
    print(f"exiftool: installed -> {EXIFTOOL_DIR}")


def _prune_insightface() -> None:
    """Drop the unused landmark models from the pack (idempotent)."""
    for onnx in INSIGHTFACE_DIR.glob("*.onnx"):
        if onnx.name not in INSIGHTFACE_KEEP:
            onnx.unlink()
            print(f"insightface: pruned unused {onnx.name}")


def download_insightface() -> None:
    if any(INSIGHTFACE_DIR.glob("*.onnx")):
        print("insightface: already present")
        _prune_insightface()
        return
    print("insightface buffalo_l: downloading (~280MB)")
    blob = _fetch(INSIGHTFACE_URL)
    INSIGHTFACE_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            # the zip may nest under buffalo_l/ — flatten to the model dir, keeping
            # only the models we actually load.
            if Path(name).name not in INSIGHTFACE_KEEP:
                continue
            target = INSIGHTFACE_DIR / Path(name).name
            target.write_bytes(zf.read(name))
    print(f"insightface: installed -> {INSIGHTFACE_DIR}")


def download_clip() -> None:
    for rel in CLIP_FILES:
        target = CLIP_DIR / rel
        if target.is_file():
            print(f"clip {rel}: already present")
            continue
        print(f"clip {rel}: downloading")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_fetch(f"{CLIP_BASE}/{rel}"))
        print(f"clip {rel}: installed -> {target}")


def download_ffmpeg() -> None:
    is_windows = platform.system().lower() == "windows"
    target = FFMPEG_DIR / ("ffmpeg.exe" if is_windows else "ffmpeg")
    if target.exists():
        print("ffmpeg: already present")
        return

    key = (platform.system().lower(), platform.machine().lower())
    slug = FFMPEG_ASSETS.get(key)
    if slug is None:
        print(f"ffmpeg: no static build mapped for {key}; skipping (poster frames disabled)")
        return

    print(f"ffmpeg: downloading ({slug})")
    FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_fetch(f"{FFMPEG_BASE}/ffmpeg-{slug}"))
    target.chmod(0o755)
    # Ship the build's license text beside the binary (GPL redistribution duty).
    (FFMPEG_DIR / "ffmpeg.LICENSE").write_bytes(_fetch(f"{FFMPEG_BASE}/{slug}.LICENSE"))
    print(f"ffmpeg: installed -> {target}")


def main() -> int:
    download_exiftool()
    download_insightface()
    download_clip()
    download_ffmpeg()
    print("\nAll build assets present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
