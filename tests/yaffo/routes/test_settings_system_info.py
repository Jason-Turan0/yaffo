from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def test_settings_system_info_shows_exiftool_and_model_paths(client, monkeypatch, tmp_path):
    exiftool = tmp_path / "bin" / "exiftool"
    classification_model = tmp_path / "cache" / "models" / "clip" / "ViT-B-32__openai"
    face_model = tmp_path / "home" / ".insightface" / "models" / "buffalo_l"

    monkeypatch.setattr("yaffo.routes.settings.get_exiftool_path", lambda: exiftool)
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
    assert "Image Classification Model:" in body
    assert str(classification_model) in body
    assert "Downloaded model cache used for image classification." in body
    assert "Face Recognition Model:" in body
    assert str(face_model) in body
    assert "Downloaded model cache used for face recognition." in body


def test_settings_system_info_shows_exiftool_missing(client, monkeypatch):
    monkeypatch.setattr("yaffo.routes.settings.get_exiftool_path", lambda: None)

    body = client.get("/settings").get_data(as_text=True)

    assert "ExifTool Path:" in body
    assert "not found" in body


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

    assert "Will be downloaded on first image-classification run." in body
    assert "Will be downloaded on first face-recognition run." in body
