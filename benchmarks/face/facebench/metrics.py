"""Accuracy metrics.

Detection (folder name = ground-truth face count):
  - exact     : fraction of photos where detected count == expected
  - MAE       : mean |detected - expected|
  - face recall: sum(min(detected, expected)) / sum(expected) -- a count proxy for
                 "found faces" (we have no per-face boxes, by design of the set)

Recognition (reference = a person's solo "1" photo; tested against their ">1"
group photos for genuine, against *other* people's solo photos for impostor):
  - recall@thr: fraction of a person's group photos where their reference matches
                at least one detected face (the metric you specified)
  - FAR@thr   : fraction of impostor pairs that falsely match
  - AUC / EER : threshold-free separation of genuine vs impostor scores, so models
                with differently-calibrated thresholds compare fairly
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from facebench.backends import Backend, Face, score
from facebench.dataset import Dataset


@dataclass
class DetectionResult:
    backend: str
    photos: int
    exact_rate: float
    mae: float
    face_recall: float
    ms_per_photo: float


@dataclass
class RecognitionResult:
    backend: str
    recall_at_threshold: float
    far_at_threshold: float
    auc: float
    eer: float
    genuine_pairs: int
    impostor_pairs: int
    ms_per_photo: float
    ms_per_face: float


def detection_result(backend_name: str, counts: list[tuple[int, int]], total_ms: float) -> DetectionResult:
    n = len(counts)
    exact = sum(1 for det, exp in counts if det == exp) / n if n else 0.0
    mae = sum(abs(det - exp) for det, exp in counts) / n if n else 0.0
    found = sum(min(det, exp) for det, exp in counts)
    expected = sum(exp for _, exp in counts)
    return DetectionResult(
        backend=backend_name,
        photos=n,
        exact_rate=exact,
        mae=mae,
        face_recall=(found / expected) if expected else 0.0,
        ms_per_photo=(total_ms / n) if n else 0.0,
    )


def _largest(faces: list[Face]) -> Face | None:
    return max(faces, key=lambda f: f.box.area) if faces else None


def _auc(genuine: np.ndarray, impostor: np.ndarray) -> float:
    if not len(genuine) or not len(impostor):
        return float("nan")
    wins = sum(float((g > impostor).sum() + 0.5 * (g == impostor).sum()) for g in genuine)
    return wins / (len(genuine) * len(impostor))


def _eer(genuine: np.ndarray, impostor: np.ndarray) -> float:
    if not len(genuine) or not len(impostor):
        return float("nan")
    thresholds = np.unique(np.concatenate([genuine, impostor]))
    best = 1.0
    for thr in thresholds:
        frr = float((genuine < thr).mean())   # genuine rejected
        far = float((impostor >= thr).mean())  # impostor accepted
        best = min(best, max(frr, far))
    return best


def recognition_result(
    dataset: Dataset,
    backend: Backend,
    faces_by_path: dict[str, list[Face]],
    analyze_ms: float,
    faces_seen: int,
    photos_seen: int,
) -> RecognitionResult:
    metric = backend.metric

    # Build per-person galleries from the solo reference photos.
    galleries: dict[str, list[np.ndarray]] = {}
    for person in dataset.persons.values():
        embs = []
        for ref in person.references:
            face = _largest(faces_by_path.get(str(ref.path), []))
            if face is not None and face.embedding is not None:
                embs.append(face.embedding)
        if embs:
            galleries[person.name] = embs

    genuine: list[float] = []
    matched = 0
    genuine_photos = 0
    for person in dataset.persons.values():
        gallery = galleries.get(person.name)
        if not gallery:
            continue
        for group in person.groups:
            faces = faces_by_path.get(str(group.path), [])
            face_embs = [f.embedding for f in faces if f.embedding is not None]
            genuine_photos += 1
            if not face_embs:
                genuine.append(float("-inf"))  # detected nothing -> a miss
                continue
            best = max(score(metric, ref, fe) for ref in gallery for fe in face_embs)
            genuine.append(best)
            if best >= backend.threshold:
                matched += 1

    # Impostor: each person's gallery vs every *other* person's reference faces.
    impostor: list[float] = []
    for a in dataset.persons.values():
        gallery = galleries.get(a.name)
        if not gallery:
            continue
        for b in dataset.persons.values():
            if b.name == a.name:
                continue
            for ref in b.references:
                face = _largest(faces_by_path.get(str(ref.path), []))
                if face is None or face.embedding is None:
                    continue
                impostor.append(max(score(metric, ref_emb, face.embedding) for ref_emb in gallery))

    # AUC/EER use finite genuine scores; recall above already counted -inf as misses.
    g = np.array([x for x in genuine if x != float("-inf")] or [0.0])
    i = np.array(impostor or [0.0])
    recall = (matched / genuine_photos) if genuine_photos else 0.0
    far = float((i >= backend.threshold).mean()) if impostor else float("nan")

    return RecognitionResult(
        backend=backend.name,
        recall_at_threshold=recall,
        far_at_threshold=far,
        auc=_auc(g, i),
        eer=_eer(g, i),
        genuine_pairs=genuine_photos,
        impostor_pairs=len(impostor),
        ms_per_photo=(analyze_ms / photos_seen) if photos_seen else 0.0,
        ms_per_face=(analyze_ms / faces_seen) if faces_seen else 0.0,
    )
