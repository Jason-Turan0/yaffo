"""Face detection + embedding via InsightFace (SCRFD detector + ArcFace embedder).

Replaces the dlib HOG detector + dlib 128-d ResNet embedder. On the Turan-Benchmark
set this was ~20x faster at detection and far more accurate (detection recall 99%
vs 79%; recognition AUC 0.993 vs 0.848) -- see benchmarks/face/. Embeddings are
512-d, L2-normalized float32, compared by cosine similarity (the matching code in
domain/compare_utils already uses cosine).

The model (`buffalo_l`, ~280MB) auto-downloads to ~/.insightface on first use and
loads lazily as a per-process singleton -- so a spawn worker pays the load once,
and the host (which never imports task code) never loads it at all. Only the
detection + recognition sub-models are loaded; gender/age/landmark extras are not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from yaffo.logging_config import get_logger

logger = get_logger(__name__, "background_tasks")

MODEL_NAME = "buffalo_l"
DET_SIZE = (640, 640)
EMBEDDING_DIM = 512

_app = None


def _get_app():
    global _app
    if _app is None:
        from insightface.app import FaceAnalysis
        logger.info(f"loading InsightFace model '{MODEL_NAME}' (CPU)")
        _app = FaceAnalysis(
            name=MODEL_NAME,
            providers=["CPUExecutionProvider"],
            # genderage adds a cheap per-face age estimate, aggregated later into a
            # person's birthdate; landmark modules stay off.
            allowed_modules=["detection", "recognition", "genderage"],
        )
        _app.prepare(ctx_id=-1, det_size=DET_SIZE)
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
    app = _get_app()
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
