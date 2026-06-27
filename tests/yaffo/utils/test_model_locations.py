import pytest

from yaffo.utils import face_analysis, image_classifier

pytestmark = pytest.mark.unit


def test_face_model_location_prefers_bundled(monkeypatch, tmp_path):
    bundled_models = tmp_path / "bundle" / "models"
    bundled = bundled_models / "insightface" / "models" / face_analysis.MODEL_NAME
    bundled.mkdir(parents=True)

    monkeypatch.setattr(face_analysis, "BUNDLED_MODELS_DIR", bundled_models)

    location = face_analysis.get_model_location()

    assert location.path == bundled
    assert location.source == "bundled"


def test_face_model_location_falls_back_to_downloaded(monkeypatch, tmp_path):
    bundled_models = tmp_path / "bundle" / "models"
    home = tmp_path / "home"
    downloaded = home / ".insightface" / "models" / face_analysis.MODEL_NAME
    downloaded.mkdir(parents=True)

    monkeypatch.setattr(face_analysis, "BUNDLED_MODELS_DIR", bundled_models)
    monkeypatch.setattr(face_analysis.Path, "home", lambda: home)

    location = face_analysis.get_model_location()

    assert location.path == downloaded
    assert location.source == "downloaded"


def test_face_model_location_pending_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(face_analysis, "BUNDLED_MODELS_DIR", tmp_path / "bundle" / "models")
    monkeypatch.setattr(face_analysis.Path, "home", lambda: tmp_path / "home")

    location = face_analysis.get_model_location()

    assert location.path is None
    assert location.source == "pending"


def test_classification_model_location_prefers_bundled(monkeypatch, tmp_path):
    bundled_models = tmp_path / "bundle" / "models"
    bundled = bundled_models / "clip" / image_classifier.MODEL_NAME
    (bundled / "visual").mkdir(parents=True)
    (bundled / "visual" / "model.onnx").touch()

    monkeypatch.setattr(image_classifier, "BUNDLED_MODELS_DIR", bundled_models)

    location = image_classifier.get_model_location()

    assert location.path == bundled
    assert location.source == "bundled"


def test_classification_model_location_falls_back_to_downloaded(monkeypatch, tmp_path):
    bundled_models = tmp_path / "bundle" / "models"
    model_cache = tmp_path / "cache" / "models"
    downloaded = model_cache / "clip" / image_classifier.MODEL_NAME
    (downloaded / "textual").mkdir(parents=True)
    (downloaded / "textual" / "model.onnx").touch()

    monkeypatch.setattr(image_classifier, "BUNDLED_MODELS_DIR", bundled_models)
    monkeypatch.setattr(image_classifier, "MODEL_CACHE_DIR", model_cache)

    location = image_classifier.get_model_location()

    assert location.path == downloaded
    assert location.source == "downloaded"


def test_classification_model_location_pending_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(image_classifier, "BUNDLED_MODELS_DIR", tmp_path / "bundle" / "models")
    monkeypatch.setattr(image_classifier, "MODEL_CACHE_DIR", tmp_path / "cache" / "models")

    location = image_classifier.get_model_location()

    assert location.path is None
    assert location.source == "pending"
