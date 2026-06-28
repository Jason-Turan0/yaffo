from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def test_settings_system_info_shows_exiftool_and_model_paths(client, monkeypatch, tmp_path):
    exiftool = tmp_path / "bin" / "exiftool"
    ffmpeg = tmp_path / "bin" / "ffmpeg"
    classification_model = tmp_path / "cache" / "models" / "clip" / "ViT-B-32__openai"
    face_model = tmp_path / "home" / ".insightface" / "models" / "buffalo_l"

    monkeypatch.setattr("yaffo.routes.settings.get_exiftool_path", lambda: exiftool)
    monkeypatch.setattr("yaffo.routes.settings.get_ffmpeg_path", lambda: ffmpeg)
    monkeypatch.setattr(
        "yaffo.routes.settings.get_classification_model_location",
        lambda: SimpleNamespace(path=classification_model, source="downloaded"),
    )
    monkeypatch.setattr(
        "yaffo.routes.settings.get_face_model_location",
        lambda: SimpleNamespace(path=face_model, source="downloaded"),
    )

    body = client.get("/settings").get_data(as_text=True)

    assert "ExifTool Path:" in body
    assert str(exiftool) in body
    assert "FFmpeg Path:" in body
    assert str(ffmpeg) in body
    assert "Image Classification Model:" in body
    assert str(classification_model) in body
    assert "Downloaded model cache used for image classification." in body
    assert "Face Recognition Model:" in body
    assert str(face_model) in body
    assert "Downloaded model cache used for face recognition." in body
    assert "An application component failed to download." not in body


def test_settings_system_info_shows_exiftool_missing(client, monkeypatch):
    monkeypatch.setattr("yaffo.routes.settings.get_exiftool_path", lambda: None)
    monkeypatch.setattr("yaffo.routes.settings.get_ffmpeg_path", lambda: None)

    body = client.get("/settings").get_data(as_text=True)

    assert "ExifTool Path:" in body
    assert "FFmpeg Path:" in body
    assert "Not found" in body
    assert "An application component failed to download." in body
    assert "Double check network settings and restart the application." in body
    assert 'data-icon="warning"' in body


def test_settings_system_info_shows_pending_model_downloads(client, monkeypatch):
    monkeypatch.setattr(
        "yaffo.routes.settings.get_classification_model_location",
        lambda: SimpleNamespace(path=None, source="pending"),
    )
    monkeypatch.setattr(
        "yaffo.routes.settings.get_face_model_location",
        lambda: SimpleNamespace(path=None, source="pending"),
    )

    body = client.get("/settings").get_data(as_text=True)

    assert "Not found" in body
    assert "Will be downloaded on app start." in body
    assert "An application component failed to download." in body
