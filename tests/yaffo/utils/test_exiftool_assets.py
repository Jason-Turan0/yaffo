import io
import zipfile

import pytest

from yaffo import download_assets
from yaffo.utils import exiftool_path

pytestmark = pytest.mark.unit


def test_exiftool_source_urls_use_sourceforge():
    assert download_assets.EXIFTOOL_SOURCE_URLS[0] == (
        "https://sourceforge.net/projects/exiftool/files/Image-ExifTool-13.40.tar.gz/download"
    )
    assert all("exiftool.org" not in url for url in download_assets.EXIFTOOL_SOURCE_URLS)


def test_exiftool_path_uses_windows_64_layout(monkeypatch, tmp_path):
    exe = tmp_path / "Image-ExifTool-13.40" / "bin" / "exiftool-13.40_64" / "exiftool.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()

    monkeypatch.setattr(exiftool_path, "EXIFTOOL_DIR", tmp_path / "Image-ExifTool-13.40")
    monkeypatch.setattr(exiftool_path, "IS_WINDOWS_64", True)
    monkeypatch.setattr(exiftool_path, "IS_WINDOWS_32", False)

    assert exiftool_path.get_exiftool_path() == exe


def test_exiftool_path_uses_source_layout(monkeypatch, tmp_path):
    script = tmp_path / "Image-ExifTool-13.40" / "src" / "exiftool"
    script.parent.mkdir(parents=True)
    script.touch()

    monkeypatch.setattr(exiftool_path, "EXIFTOOL_DIR", tmp_path / "Image-ExifTool-13.40")
    monkeypatch.setattr(exiftool_path, "IS_WINDOWS_64", False)
    monkeypatch.setattr(exiftool_path, "IS_WINDOWS_32", False)

    assert exiftool_path.get_exiftool_path() == script


def test_download_exiftool_windows_normalizes_executable(monkeypatch, tmp_path):
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as zf:
        zf.writestr("exiftool-13.40_64/exiftool(-k).exe", b"exe")

    monkeypatch.setattr(download_assets, "EXIFTOOL_DIR", tmp_path / "Image-ExifTool-13.40")
    monkeypatch.setattr(download_assets.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(download_assets, "_fetch", lambda url: blob.getvalue())

    download_assets._download_exiftool_windows()

    target = tmp_path / "Image-ExifTool-13.40" / "bin" / "exiftool-13.40_64" / "exiftool.exe"
    assert target.read_bytes() == b"exe"
