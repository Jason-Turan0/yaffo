"""Face detection + embedding via InsightFace (SCRFD detector + ArcFace embedder).

Replaces the dlib HOG detector + dlib 128-d ResNet embedder. On the Turan-Benchmark
set this was ~20x faster at detection and far more accurate (detection recall 99%
vs 79%; recognition AUC 0.993 vs 0.848) -- see benchmarks/face/. Embeddings are
512-d, L2-normalized float32, compared by cosine similarity (the matching code in
domain/compare_utils already uses cosine).

The model (`buffalo_l`, ~280MB) is downloaded to ROOT_DIR/models on app start and
loads lazily as a per-process singleton -- so a spawn worker pays the load once,
and the host (which never imports task code) never loads it at all. Only the
detection + recognition sub-models are loaded; gender/age/landmark extras are not.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from yaffo.common import MODEL_CACHE_DIR
from yaffo.logging_config import get_logger

logger = get_logger(__name__, "background_tasks")

MODEL_NAME = "buffalo_l"
REQUIRED_MODEL_FILES = {"det_10g.onnx", "w600k_r50.onnx", "genderage.onnx"}
DEFAULT_DETECTION_THRESHOLD = "medium"
DEFAULT_DETECTION_SIZE = "medium"
DETECTION_THRESHOLDS = {
    "low": 0.35,
    "medium": 0.5,
    "high": 0.65,
}
DETECTION_SIZES = {
    "small": (320, 320),
    "medium": (640, 640),
    "large": (960, 960),
}
DET_SIZE = DETECTION_SIZES[DEFAULT_DETECTION_SIZE]
EMBEDDING_DIM = 512

_app = None
_app_settings = None
_load_failed = False


@dataclass(frozen=True)
class ModelLocation:
    path: Path | None
    source: str


@dataclass(frozen=True)
class DetectionSettings:
    threshold: float
    size: tuple[int, int]


def _model_root() -> str:
    """InsightFace loads models from `<root>/models/<name>`."""
    return str(MODEL_CACHE_DIR / "insightface")


def get_model_location() -> ModelLocation:
    downloaded = MODEL_CACHE_DIR / "insightface" / "models" / MODEL_NAME
    if all((downloaded / model_file).is_file() for model_file in REQUIRED_MODEL_FILES):
        return ModelLocation(path=downloaded, source="downloaded")

    return ModelLocation(path=None, source="pending")


def download_model():
    from yaffo.download_assets import download_insightface

    download_insightface()


def _preset(value, default: str, supported: dict) -> str:
    return value if isinstance(value, str) and value in supported else default


def get_detection_settings() -> DetectionSettings:
    """Read File Sync face-detection presets, falling back to today's defaults."""
    threshold_preset = DEFAULT_DETECTION_THRESHOLD
    size_preset = DEFAULT_DETECTION_SIZE
    try:
        from yaffo.db import db
        from yaffo.db.models import Automation, AUTOMATION_HANDLER_FILE_SYNC

        automation = (
            db.session.query(Automation)
            .filter(Automation.handler == AUTOMATION_HANDLER_FILE_SYNC)
            .first()
        )
        config = automation.config if automation is not None else None
        if isinstance(config, dict):
            threshold_preset = _preset(
                config.get("face_detection_threshold"),
                DEFAULT_DETECTION_THRESHOLD,
                DETECTION_THRESHOLDS,
            )
            size_preset = _preset(
                config.get("face_detection_size"),
                DEFAULT_DETECTION_SIZE,
                DETECTION_SIZES,
            )
    except RuntimeError:
        pass

    return DetectionSettings(
        threshold=DETECTION_THRESHOLDS[threshold_preset],
        size=DETECTION_SIZES[size_preset],
    )


def get_app():
    global _app, _app_settings
    if get_model_location().path is None:
        raise FileNotFoundError(f"InsightFace model package is not installed: {MODEL_NAME}")
    settings = get_detection_settings()
    if _app is None:
        from insightface.app import FaceAnalysis
        logger.info(f"loading InsightFace model '{MODEL_NAME}' (CPU)")
        _app = FaceAnalysis(
            name=MODEL_NAME,
            root=_model_root(),
            providers=["CPUExecutionProvider"],
            # genderage adds a cheap per-face age estimate, aggregated later into a
            # person's birthdate; landmark modules stay off.
            allowed_modules=["detection", "recognition", "genderage"],
        )
    if _app_settings != settings:
        logger.info(
            "preparing InsightFace detector threshold=%s det_size=%s",
            settings.threshold,
            settings.size,
        )
        _app.prepare(ctx_id=-1, det_thresh=settings.threshold, det_size=settings.size)
        _app_settings = settings
    return _app


@dataclass
class DetectedFace:
    """A face found in an image. Box is in dlib's (top, right, bottom, left)
    convention so it drops into the existing Face rows / thumbnail crop unchanged.
    `embedding` is a 512-d L2-normalized float32 ArcFace vector. `age` is the
    genderage model's predicted age in years; `gender` is 0=female/1=male;
    `det_score` is the detector's confidence (0-1). (None if unavailable.)"""
    location_top: int
    location_right: int
    location_bottom: int
    location_left: int
    embedding: np.ndarray
    age: Optional[float] = None
    gender: Optional[int] = None
    det_score: Optional[float] = None


def detect_faces(image_rgb: np.ndarray) -> list[DetectedFace]:
    """Detect faces in an RGB image and return their boxes + ArcFace embeddings.
    Boxes are clamped to the image bounds."""
    global _load_failed
    if _load_failed:
        return []
    try:
        app = get_app()
    except Exception as e:  # noqa: BLE001 - missing/broken model should not fail indexing
        _load_failed = True
        logger.warning("InsightFace unavailable; skipping face detection: %s", e)
        return []
    h, w = image_rgb.shape[:2]
    bgr = np.ascontiguousarray(image_rgb[:, :, ::-1])  # InsightFace expects BGR
    out: list[DetectedFace] = []
    for f in app.get(bgr):
        x1, y1, x2, y2 = f.bbox
        top, left = max(0, int(round(y1))), max(0, int(round(x1)))
        bottom, right = min(h, int(round(y2))), min(w, int(round(x2)))
        if bottom <= top or right <= left:
            continue
        embedding = np.asarray(f.normed_embedding, dtype=np.float32)
        if not np.all(np.isfinite(embedding)):
            continue  # degenerate crop produced a NaN/inf embedding; skip it
        age = getattr(f, "age", None)
        gender = getattr(f, "gender", None)
        det_score = getattr(f, "det_score", None)
        out.append(DetectedFace(
            location_top=top,
            location_right=right,
            location_bottom=bottom,
            location_left=left,
            embedding=embedding,
            age=float(age) if age is not None else None,
            gender=int(gender) if gender is not None else None,
            det_score=float(det_score) if det_score is not None else None,
        ))
    return out
