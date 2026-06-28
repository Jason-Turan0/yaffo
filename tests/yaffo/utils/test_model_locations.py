import pytest

from yaffo.utils import face_analysis, image_classifier

pytestmark = pytest.mark.unit


def test_face_model_location_uses_root_models(monkeypatch, tmp_path):
    model_cache = tmp_path / "models"
    downloaded = model_cache / "insightface" / "models" / face_analysis.MODEL_NAME
    downloaded.mkdir(parents=True)
    (downloaded / "det_10g.onnx").touch()

    monkeypatch.setattr(face_analysis, "MODEL_CACHE_DIR", model_cache)

    location = face_analysis.get_model_location()

    assert location.path == downloaded
    assert location.source == "downloaded"


def test_face_model_location_pending_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(face_analysis, "MODEL_CACHE_DIR", tmp_path / "models")

    location = face_analysis.get_model_location()

    assert location.path is None
    assert location.source == "pending"


def test_classification_model_location_uses_root_models(monkeypatch, tmp_path):
    model_cache = tmp_path / "cache" / "models"
    downloaded = model_cache / "clip" / image_classifier.MODEL_NAME
    (downloaded / "textual").mkdir(parents=True)
    (downloaded / "textual" / "model.onnx").touch()

    monkeypatch.setattr(image_classifier, "MODEL_CACHE_DIR", model_cache)

    location = image_classifier.get_model_location()

    assert location.path == downloaded
    assert location.source == "downloaded"


def test_classification_model_location_pending_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(image_classifier, "MODEL_CACHE_DIR", tmp_path / "cache" / "models")

    location = image_classifier.get_model_location()

    assert location.path is None
    assert location.source == "pending"
