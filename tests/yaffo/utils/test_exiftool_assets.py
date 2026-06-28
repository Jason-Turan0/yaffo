import io
import zipfile

import pytest

from yaffo import download_assets
from yaffo.utils import exiftool_path

pytestmark = pytest.mark.unit


def test_exiftool_source_url_uses_sourceforge():
    assert download_assets._exiftool_source_url("13.59") == (
        "https://sourceforge.net/projects/exiftool/files/Image-ExifTool-13.59.tar.gz/download"
    )


def test_latest_exiftool_version_uses_sourceforge_listing(monkeypatch):
    html = """
    <a href="/projects/exiftool/files/Image-ExifTool-13.40.tar.gz/download">old</a>
    <a href="/projects/exiftool/files/Image-ExifTool-13.59.tar.gz/download">latest</a>
    """
    monkeypatch.setattr(download_assets, "_fetch_text", lambda url: html)

    assert download_assets._latest_exiftool_version(r"Image-ExifTool-(\d+\.\d+)\.tar\.gz") == "13.59"


def test_exiftool_path_uses_windows_64_layout(monkeypatch, tmp_path):
    exe = tmp_path / "Image-ExifTool-13.59" / "bin" / "exiftool-13.59_64" / "exiftool.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()

    monkeypatch.setattr(exiftool_path, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(exiftool_path, "IS_WINDOWS_64", True)
    monkeypatch.setattr(exiftool_path, "IS_WINDOWS_32", False)

    assert exiftool_path.get_exiftool_path() == exe


def test_exiftool_path_uses_source_layout(monkeypatch, tmp_path):
    script = tmp_path / "Image-ExifTool-13.59" / "src" / "exiftool"
    script.parent.mkdir(parents=True)
    script.touch()

    monkeypatch.setattr(exiftool_path, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(exiftool_path, "IS_WINDOWS_64", False)
    monkeypatch.setattr(exiftool_path, "IS_WINDOWS_32", False)

    assert exiftool_path.get_exiftool_path() == script


def test_exiftool_path_prefers_latest_installed_version(monkeypatch, tmp_path):
    old = tmp_path / "Image-ExifTool-13.40" / "src" / "exiftool"
    latest = tmp_path / "Image-ExifTool-13.59" / "src" / "exiftool"
    old.parent.mkdir(parents=True)
    latest.parent.mkdir(parents=True)
    old.touch()
    latest.touch()

    monkeypatch.setattr(exiftool_path, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(exiftool_path, "IS_WINDOWS_64", False)
    monkeypatch.setattr(exiftool_path, "IS_WINDOWS_32", False)

    assert exiftool_path.get_exiftool_path() == latest


def test_download_exiftool_windows_normalizes_executable(monkeypatch, tmp_path):
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as zf:
        zf.writestr("exiftool-13.59_64/exiftool(-k).exe", b"exe")

    listing = b'<a href="/projects/exiftool/files/exiftool-13.59_64.zip/download">latest</a>'
    payloads = [listing, blob.getvalue()]
    monkeypatch.setattr(download_assets, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(download_assets.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(download_assets, "_fetch", lambda url: payloads.pop(0))

    download_assets._download_exiftool_windows()

    target = tmp_path / "Image-ExifTool-13.59" / "bin" / "exiftool-13.59_64" / "exiftool.exe"
    assert target.read_bytes() == b"exe"
