import pytest
import numpy as np
import sys
import types

from yaffo.utils import face_analysis, image_classifier
from yaffo.app import create_app
from yaffo.db import db
from yaffo.db.models import Automation, AUTOMATION_STATUS_READY

pytestmark = pytest.mark.unit


@pytest.fixture
def app(tmp_path):
    application = create_app(db_path=tmp_path / "test.db", config={"TESTING": True})
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


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
    monkeypatch.setattr(face_analysis, "_app_settings", None)
    monkeypatch.setattr(face_analysis, "_load_failed", False)

    faces = face_analysis.detect_faces(np.zeros((10, 10, 3), dtype=np.uint8))

    assert faces == []


def test_face_app_uses_file_sync_detection_config(app, monkeypatch, tmp_path):
    model_cache = tmp_path / "models"
    downloaded = model_cache / "insightface" / "models" / face_analysis.MODEL_NAME
    downloaded.mkdir(parents=True)
    for model_file in face_analysis.REQUIRED_MODEL_FILES:
        (downloaded / model_file).touch()

    prepared = []

    class FakeFaceAnalysis:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def prepare(self, **kwargs):
            prepared.append(kwargs)

    insightface_module = types.ModuleType("insightface")
    insightface_app_module = types.ModuleType("insightface.app")
    insightface_app_module.FaceAnalysis = FakeFaceAnalysis
    monkeypatch.setitem(sys.modules, "insightface", insightface_module)
    monkeypatch.setitem(sys.modules, "insightface.app", insightface_app_module)
    monkeypatch.setattr(face_analysis, "MODEL_CACHE_DIR", model_cache)
    monkeypatch.setattr(face_analysis, "_app", None)
    monkeypatch.setattr(face_analysis, "_app_settings", None)

    with app.app_context():
        db.session.add(Automation(
            slug="file_sync",
            name="File sync",
            is_system=True,
            handler="file_sync",
            status=AUTOMATION_STATUS_READY,
            config={
                "face_detection_threshold": "low",
                "face_detection_size": "large",
            },
        ))
        db.session.commit()

        face_analysis.get_app()

    assert prepared == [{
        "ctx_id": -1,
        "det_thresh": 0.35,
        "det_size": (960, 960),
    }]


def test_face_app_reprepares_when_detection_config_changes(app, monkeypatch, tmp_path):
    model_cache = tmp_path / "models"
    downloaded = model_cache / "insightface" / "models" / face_analysis.MODEL_NAME
    downloaded.mkdir(parents=True)
    for model_file in face_analysis.REQUIRED_MODEL_FILES:
        (downloaded / model_file).touch()

    prepared = []

    class FakeFaceAnalysis:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def prepare(self, **kwargs):
            prepared.append(kwargs)

    insightface_module = types.ModuleType("insightface")
    insightface_app_module = types.ModuleType("insightface.app")
    insightface_app_module.FaceAnalysis = FakeFaceAnalysis
    monkeypatch.setitem(sys.modules, "insightface", insightface_module)
    monkeypatch.setitem(sys.modules, "insightface.app", insightface_app_module)
    monkeypatch.setattr(face_analysis, "MODEL_CACHE_DIR", model_cache)
    monkeypatch.setattr(face_analysis, "_app", None)
    monkeypatch.setattr(face_analysis, "_app_settings", None)

    with app.app_context():
        automation = Automation(
            slug="file_sync",
            name="File sync",
            is_system=True,
            handler="file_sync",
            status=AUTOMATION_STATUS_READY,
            config={
                "face_detection_threshold": "medium",
                "face_detection_size": "medium",
            },
        )
        db.session.add(automation)
        db.session.commit()

        face_analysis.get_app()
        automation.config = {
            "face_detection_threshold": "high",
            "face_detection_size": "small",
        }
        db.session.commit()
        face_analysis.get_app()

    assert prepared == [
        {"ctx_id": -1, "det_thresh": 0.5, "det_size": (640, 640)},
        {"ctx_id": -1, "det_thresh": 0.65, "det_size": (320, 320)},
    ]


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
