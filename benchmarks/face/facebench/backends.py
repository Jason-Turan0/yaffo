"""Pluggable face-pipeline backends.

Each backend exposes a uniform surface so the runner can treat them identically:
  - ``detect(rgb)``  -> list[Box]            (for the detection-count benchmark + timing)
  - ``analyze(rgb)`` -> list[Face]           (boxes + embeddings, for recognition)
  - ``metric`` / ``threshold``               (how to compare embeddings)

`detect` and `analyze` are timed independently, so detection cost is isolated from
embedding cost even for fused pipelines. Backends whose library isn't installed are
skipped (the runner reports which), so you can add them incrementally.

Embeddings for cosine backends are returned L2-normalized, so cosine == dot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class Box:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)


@dataclass
class Face:
    box: Box
    embedding: Optional[np.ndarray]


def score(metric: str, a: np.ndarray, b: np.ndarray) -> float:
    """Similarity in a 'higher = more alike' space for both metrics, so thresholds
    and ROC are directionally consistent across backends."""
    if metric == "cosine":
        return float(np.dot(a, b))          # inputs are unit-normalized
    return -float(np.linalg.norm(a - b))    # euclidean -> negative distance


def _l2(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


class Backend:
    name: str = "base"
    has_embeddings: bool = True
    metric: str = "cosine"
    threshold: float = 0.0  # in score() space (higher = match)

    def load(self) -> None: ...
    def detect(self, rgb: np.ndarray) -> list[Box]: raise NotImplementedError
    def analyze(self, rgb: np.ndarray) -> list[Face]: raise NotImplementedError


# --------------------------------------------------------------------------- #
# OpenCV: YuNet detector + SFace recognizer (single dependency, no source build)
# --------------------------------------------------------------------------- #
class OpenCVBackend(Backend):
    name = "opencv-yunet-sface"
    metric = "cosine"
    threshold = 0.363  # OpenCV's documented SFace cosine match threshold

    def load(self) -> None:
        import cv2
        from facebench.models import ensure_model
        self._cv2 = cv2
        self._det = cv2.FaceDetectorYN.create(
            str(ensure_model("yunet")), "", (320, 320),
            score_threshold=0.6, nms_threshold=0.3, top_k=5000,
        )
        self._rec = cv2.FaceRecognizerSF.create(str(ensure_model("sface")), "")

    def _raw(self, rgb: np.ndarray):
        bgr = rgb[:, :, ::-1]
        h, w = bgr.shape[:2]
        self._det.setInputSize((w, h))
        _, faces = self._det.detect(bgr)
        return bgr, (faces if faces is not None else [])

    def detect(self, rgb: np.ndarray) -> list[Box]:
        _, faces = self._raw(rgb)
        return [Box(r[0], r[1], r[0] + r[2], r[1] + r[3]) for r in faces]

    def analyze(self, rgb: np.ndarray) -> list[Face]:
        bgr, faces = self._raw(rgb)
        out = []
        for r in faces:
            aligned = self._rec.alignCrop(bgr, r)
            emb = _l2(self._rec.feature(aligned).flatten())
            out.append(Face(Box(r[0], r[1], r[0] + r[2], r[1] + r[3]), emb))
        return out


# --------------------------------------------------------------------------- #
# InsightFace: SCRFD detector + ArcFace embedder (buffalo_l, ONNX, CPU)
# --------------------------------------------------------------------------- #
class InsightFaceBackend(Backend):
    name = "insightface-scrfd-arcface"
    metric = "cosine"
    threshold = 0.30  # indicative ArcFace cosine threshold (AUC/EER are threshold-free)

    def load(self) -> None:
        from insightface.app import FaceAnalysis
        self._app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        self._app.prepare(ctx_id=-1, det_size=(640, 640))

    def detect(self, rgb: np.ndarray) -> list[Box]:
        bgr = rgb[:, :, ::-1]
        bboxes, _ = self._app.det_model.detect(bgr, max_num=0, metric="default")
        return [Box(b[0], b[1], b[2], b[3]) for b in bboxes]

    def analyze(self, rgb: np.ndarray) -> list[Face]:
        bgr = rgb[:, :, ::-1]
        faces = self._app.get(bgr)
        return [Face(Box(*f.bbox), _l2(np.asarray(f.normed_embedding))) for f in faces]


# --------------------------------------------------------------------------- #
# dlib / face_recognition: HOG detector + 128-d embedder (the current baseline)
# --------------------------------------------------------------------------- #
class DlibBackend(Backend):
    name = "dlib-hog-resnet"
    metric = "euclidean"
    threshold = -0.6  # dlib's 0.6 distance match threshold, in -distance space

    def load(self) -> None:
        import face_recognition
        self._fr = face_recognition

    def detect(self, rgb: np.ndarray) -> list[Box]:
        locs = self._fr.face_locations(rgb)
        return [Box(l, t, r, b) for (t, r, b, l) in locs]

    def analyze(self, rgb: np.ndarray) -> list[Face]:
        locs = self._fr.face_locations(rgb)
        encs = self._fr.face_encodings(rgb, locs)
        return [Face(Box(l, t, r, b), np.asarray(e)) for (t, r, b, l), e in zip(locs, encs)]


# --------------------------------------------------------------------------- #
# MediaPipe BlazeFace: detector only
# --------------------------------------------------------------------------- #
class MediaPipeBackend(Backend):
    name = "mediapipe-blazeface"
    has_embeddings = False

    def load(self) -> None:
        import mediapipe as mp
        self._fd = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.5
        )

    def detect(self, rgb: np.ndarray) -> list[Box]:
        h, w = rgb.shape[:2]
        res = self._fd.process(rgb)
        if not res.detections:
            return []
        boxes = []
        for d in res.detections:
            rb = d.location_data.relative_bounding_box
            x1, y1 = rb.xmin * w, rb.ymin * h
            boxes.append(Box(x1, y1, x1 + rb.width * w, y1 + rb.height * h))
        return boxes

    def analyze(self, rgb: np.ndarray) -> list[Face]:
        return [Face(b, None) for b in self.detect(rgb)]


# --------------------------------------------------------------------------- #
# facenet-pytorch: MTCNN detector + FaceNet (InceptionResnetV1) embedder
# --------------------------------------------------------------------------- #
class FaceNetBackend(Backend):
    name = "facenet-mtcnn"
    metric = "cosine"
    threshold = 0.40  # indicative

    def load(self) -> None:
        import torch
        from facenet_pytorch import MTCNN, InceptionResnetV1
        self._torch = torch
        self._mtcnn = MTCNN(keep_all=True, device="cpu")
        self._resnet = InceptionResnetV1(pretrained="vggface2").eval()

    def detect(self, rgb: np.ndarray) -> list[Box]:
        boxes, _ = self._mtcnn.detect(rgb)
        return [Box(*b) for b in boxes] if boxes is not None else []

    def analyze(self, rgb: np.ndarray) -> list[Face]:
        boxes, _ = self._mtcnn.detect(rgb)
        if boxes is None:
            return []
        crops = self._mtcnn.extract(rgb, boxes, save_path=None)
        with self._torch.no_grad():
            embs = self._resnet(crops)
        return [
            Face(Box(*b), _l2(e.numpy()))
            for b, e in zip(boxes, embs)
        ]


ALL_BACKENDS = [
    DlibBackend, OpenCVBackend, InsightFaceBackend, MediaPipeBackend, FaceNetBackend,
]


def build_backends(only: Optional[list[str]] = None) -> list[Backend]:
    """Instantiate + load every available backend, skipping any whose library
    isn't installed (or that fails to initialize)."""
    built = []
    for cls in ALL_BACKENDS:
        if only and cls.name not in only:
            continue
        backend = cls()
        try:
            backend.load()
        except Exception as e:
            print(f"skip {cls.name}: {type(e).__name__}: {e}")
            continue
        built.append(backend)
        print(f"loaded {cls.name}")
    return built
