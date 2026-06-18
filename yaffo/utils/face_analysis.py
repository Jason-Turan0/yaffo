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
            allowed_modules=["detection", "recognition"],
        )
        _app.prepare(ctx_id=-1, det_size=DET_SIZE)
    return _app


@dataclass
class DetectedFace:
    """A face found in an image. Box is in dlib's (top, right, bottom, left)
    convention so it drops into the existing Face rows / thumbnail crop unchanged.
    `embedding` is a 512-d L2-normalized float32 ArcFace vector."""
    location_top: int
    location_right: int
    location_bottom: int
    location_left: int
    embedding: np.ndarray


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
        out.append(DetectedFace(
            location_top=top,
            location_right=right,
            location_bottom=bottom,
            location_left=left,
            embedding=np.asarray(f.normed_embedding, dtype=np.float32),
        ))
    return out
