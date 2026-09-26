"""download_assets repairs a partial install instead of skipping it."""
import io
import tarfile
import zipfile

import pytest

from yaffo import download_assets as assets

pytestmark = pytest.mark.unit


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


@pytest.fixture
def fetched(monkeypatch):
    """Record fetched URLs; `responses` maps a URL suffix to its bytes."""
    calls = []
    responses = {}

    def fetch(url):
        calls.append(url)
        for suffix, data in responses.items():
            if url.endswith(suffix):
                return data
        raise AssertionError(f"unexpected fetch {url}")

    monkeypatch.setattr(assets, "_fetch", fetch)
    return calls, responses


def test_insightface_restores_only_the_missing_model(tmp_path, monkeypatch, fetched):
    calls, responses = fetched
    model_dir = tmp_path / "buffalo_l"
    monkeypatch.setattr(assets, "INSIGHTFACE_DIR", model_dir)
    model_dir.mkdir()
    (model_dir / "det_10g.onnx").write_bytes(b"existing det")
    (model_dir / "genderage.onnx").write_bytes(b"existing age")
    responses["buffalo_l.zip"] = _zip({
        "buffalo_l/det_10g.onnx": b"new det",
        "buffalo_l/w600k_r50.onnx": b"arcface",
        "buffalo_l/genderage.onnx": b"new age",
        "buffalo_l/2d106det.onnx": b"unused",
    })

    assets.download_insightface()

    assert (model_dir / "w600k_r50.onnx").read_bytes() == b"arcface"
    assert (model_dir / "det_10g.onnx").read_bytes() == b"existing det"
    assert sorted(p.name for p in model_dir.iterdir()) == ["det_10g.onnx", "genderage.onnx", "w600k_r50.onnx"]

    calls.clear()
    assets.download_insightface()
    assert calls == []


def test_insightface_fails_loudly_when_the_package_lacks_a_model(tmp_path, monkeypatch, fetched):
    _, responses = fetched
    monkeypatch.setattr(assets, "INSIGHTFACE_DIR", tmp_path / "buffalo_l")
    responses["buffalo_l.zip"] = _zip({"buffalo_l/det_10g.onnx": b"det"})
    with pytest.raises(RuntimeError, match="w600k_r50.onnx"):
        assets.download_insightface()


def test_clip_fetches_only_missing_encoders(tmp_path, monkeypatch, fetched):
    calls, responses = fetched
    monkeypatch.setattr(assets, "CLIP_DIR", tmp_path / "clip")
    (tmp_path / "clip" / "visual").mkdir(parents=True)
    (tmp_path / "clip" / "visual" / "model.onnx").write_bytes(b"visual")
    responses["textual/model.onnx"] = b"textual"

    assets.download_clip()

    assert [url.rsplit("/", 2)[-2] for url in calls] == ["textual"]
    assert (tmp_path / "clip" / "textual" / "model.onnx").read_bytes() == b"textual"


def test_interrupted_download_leaves_no_file_that_looks_installed(tmp_path, monkeypatch, fetched):
    _, responses = fetched
    monkeypatch.setattr(assets, "CLIP_DIR", tmp_path / "clip")
    responses["visual/model.onnx"] = b"visual"

    def interrupted(src, dst):
        raise KeyboardInterrupt
    monkeypatch.setattr(assets.os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        assets.download_clip()

    # Only the .part file exists, so the next run downloads it again.
    assert not (tmp_path / "clip" / "visual" / "model.onnx").exists()
    assert (tmp_path / "clip" / "visual" / "model.onnx.part").exists()


def test_write_atomic_replaces_in_one_step(tmp_path):
    target = tmp_path / "sub" / "ffmpeg"
    assets._write_atomic(target, b"bin", mode=0o755)
    assert target.read_bytes() == b"bin"
    assert target.stat().st_mode & 0o777 == 0o755
    assert not (tmp_path / "sub" / "ffmpeg.part").exists()


def test_ffmpeg_restores_a_missing_license_without_refetching_the_binary(tmp_path, monkeypatch, fetched):
    calls, responses = fetched
    monkeypatch.setattr(assets, "FFMPEG_DIR", tmp_path)
    monkeypatch.setattr(assets.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(assets.platform, "machine", lambda: "arm64")
    (tmp_path / "ffmpeg").write_bytes(b"binary")
    responses["darwin-arm64.LICENSE"] = b"GPL"

    assets.download_ffmpeg()

    assert calls == [f"{assets.FFMPEG_BASE}/darwin-arm64.LICENSE"]
    assert (tmp_path / "ffmpeg.LICENSE").read_bytes() == b"GPL"


def test_exiftool_source_without_its_library_is_reinstalled(tmp_path, monkeypatch, fetched):
    calls, responses = fetched
    monkeypatch.setattr(assets, "ASSET_DIR", tmp_path)
    monkeypatch.setattr(assets, "_latest_exiftool_version", lambda pattern: "13.59")
    src = tmp_path / "Image-ExifTool-13.59" / "src"
    src.mkdir(parents=True)
    (src / "exiftool").write_text("#!/usr/bin/perl")

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in {"Image-ExifTool-13.59/exiftool": b"#!/usr/bin/perl",
                           "Image-ExifTool-13.59/lib/Image/ExifTool.pm": b"package Image::ExifTool;",
                           "Image-ExifTool-13.59/README": b"readme"}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    responses["Image-ExifTool-13.59.tar.gz/download"] = buffer.getvalue()

    assets._download_exiftool_source()

    assert (src / "lib" / "Image" / "ExifTool.pm").is_file()
    assert sorted(p.name for p in src.iterdir()) == ["exiftool", "lib"]

    calls.clear()
    assets._download_exiftool_source()
    assert calls == []
