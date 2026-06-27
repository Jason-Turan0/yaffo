import pytest

pytestmark = pytest.mark.unit


def test_settings_system_info_shows_exiftool_and_model_paths(client, monkeypatch, tmp_path):
    exiftool = tmp_path / "bin" / "exiftool"
    model_cache = tmp_path / "cache" / "models"
    home = tmp_path / "home"
    bundled_models = tmp_path / "bundle" / "resources" / "models"

    monkeypatch.setattr("yaffo.routes.settings.get_exiftool_path", lambda: exiftool)
    monkeypatch.setattr("yaffo.routes.settings.MODEL_CACHE_DIR", model_cache)
    monkeypatch.setattr("yaffo.routes.settings.BUNDLED_MODELS_DIR", bundled_models)
    monkeypatch.setattr("yaffo.routes.settings.Path.home", lambda *args: home)

    body = client.get("/settings").get_data(as_text=True)

    assert "ExifTool Path:" in body
    assert str(exiftool) in body
    assert "CLIP Model Cache:" in body
    assert str(model_cache / "clip") in body
    assert "InsightFace Model Cache:" in body
    assert str(home / ".insightface") in body
    assert "Bundled Models Directory:" in body
    assert str(bundled_models) in body


def test_settings_system_info_shows_exiftool_missing(client, monkeypatch):
    monkeypatch.setattr("yaffo.routes.settings.get_exiftool_path", lambda: None)

    body = client.get("/settings").get_data(as_text=True)

    assert "ExifTool Path:" in body
    assert "not found" in body
