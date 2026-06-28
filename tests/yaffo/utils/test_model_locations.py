import pytest
import numpy as np

from yaffo.utils import face_analysis, image_classifier

pytestmark = pytest.mark.unit


def test_face_model_location_uses_root_models(monkeypatch, tmp_path):
    model_cache = tmp_path / "models"
    downloaded = model_cache / "insightface" / "models" / face_analysis.MODEL_NAME
    downloaded.mkdir(parents=True)
    for model_file in face_analysis.REQUIRED_MODEL_FILES:
        (downloaded / model_file).touch()

    monkeypatch.setattr(face_analysis, "MODEL_CACHE_DIR", model_cache)

    location = face_analysis.get_model_location()

    assert location.path == downloaded
    assert location.source == "downloaded"


def test_face_model_location_pending_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(face_analysis, "MODEL_CACHE_DIR", tmp_path / "models")

    location = face_analysis.get_model_location()

    assert location.path is None
    assert location.source == "pending"


def test_face_model_requires_complete_package(monkeypatch, tmp_path):
    model_cache = tmp_path / "models"
    downloaded = model_cache / "insightface" / "models" / face_analysis.MODEL_NAME
    downloaded.mkdir(parents=True)
    (downloaded / "det_10g.onnx").touch()

    monkeypatch.setattr(face_analysis, "MODEL_CACHE_DIR", model_cache)

    location = face_analysis.get_model_location()

    assert location.path is None
    assert location.source == "pending"


def test_detect_faces_returns_empty_when_model_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(face_analysis, "MODEL_CACHE_DIR", tmp_path / "models")
    monkeypatch.setattr(face_analysis, "_app", None)
    monkeypatch.setattr(face_analysis, "_load_failed", False)

    faces = face_analysis.detect_faces(np.zeros((10, 10, 3), dtype=np.uint8))

    assert faces == []


def test_classification_model_location_uses_root_models(monkeypatch, tmp_path):
    model_cache = tmp_path / "cache" / "models"
    downloaded = model_cache / "clip" / image_classifier.MODEL_NAME
    for model_file in image_classifier._FILES.values():
        (downloaded / model_file).parent.mkdir(parents=True, exist_ok=True)
        (downloaded / model_file).touch()

    monkeypatch.setattr(image_classifier, "MODEL_CACHE_DIR", model_cache)

    location = image_classifier.get_model_location()

    assert location.path == downloaded
    assert location.source == "downloaded"


def test_classification_model_location_pending_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(image_classifier, "MODEL_CACHE_DIR", tmp_path / "cache" / "models")

    location = image_classifier.get_model_location()

    assert location.path is None
    assert location.source == "pending"


def test_classification_model_requires_complete_package(monkeypatch, tmp_path):
    model_cache = tmp_path / "cache" / "models"
    downloaded = model_cache / "clip" / image_classifier.MODEL_NAME
    (downloaded / "textual").mkdir(parents=True)
    (downloaded / "textual" / "model.onnx").touch()

    monkeypatch.setattr(image_classifier, "MODEL_CACHE_DIR", model_cache)

    location = image_classifier.get_model_location()

    assert location.path is None
    assert location.source == "pending"


def test_ensure_model_does_not_download_from_job(monkeypatch, tmp_path):
    monkeypatch.setattr(image_classifier, "MODEL_CACHE_DIR", tmp_path / "cache" / "models")

    with pytest.raises(FileNotFoundError):
        image_classifier.ensure_model("visual")
