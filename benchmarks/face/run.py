"""Run the face detection + recognition benchmark over the Turan-Benchmark set.

Usage:
    python run.py [--dataset DIR] [--backends a,b] [--limit N] [--json OUT.json]

For each available backend it measures:
  - detection: face-count accuracy vs the folder labels + wall time per photo,
  - recognition: does a person's solo reference match a face in their group photos
    (recall), plus impostor FAR, AUC, EER, and embedding wall time.

Backends whose library isn't installed are skipped, so start with requirements.txt
(OpenCV + InsightFace) and add the rest from requirements-optional.txt.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from facebench.backends import build_backends
from facebench.dataset import load_dataset
from facebench.imageio import load_rgb
from facebench.metrics import detection_result, recognition_result

DEFAULT_DATASET = Path("/Users/jason.turan/Pictures/Turan-Benchmark")


def _safe_load(path: Path):
    try:
        return load_rgb(path)
    except Exception as e:
        print(f"  skip unreadable {path.name}: {type(e).__name__}: {e}")
        return None


def _print_detection(rows):
    print("\n=== DETECTION (face count vs folder label) ===")
    print(f"{'backend':<28}{'photos':>7}{'exact':>8}{'MAE':>7}{'recall':>8}{'ms/photo':>10}")
    print("-" * 68)
    for r in rows:
        print(f"{r.backend:<28}{r.photos:>7}{r.exact_rate*100:>7.0f}%{r.mae:>7.2f}"
              f"{r.face_recall*100:>7.0f}%{r.ms_per_photo:>10.0f}")


def _print_recognition(rows):
    print("\n=== RECOGNITION (reference -> group photo) ===")
    print(f"{'backend':<28}{'recall':>8}{'FAR':>7}{'AUC':>7}{'EER':>7}"
          f"{'gen':>5}{'imp':>5}{'ms/face':>9}")
    print("-" * 76)
    for r in rows:
        print(f"{r.backend:<28}{r.recall_at_threshold*100:>7.0f}%{r.far_at_threshold*100:>6.0f}%"
              f"{r.auc:>7.3f}{r.eer*100:>6.0f}%{r.genuine_pairs:>5}{r.impostor_pairs:>5}"
              f"{r.ms_per_face:>9.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Face detection/recognition benchmark")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--backends", type=str, default=None,
                        help="comma-separated backend names (default: all available)")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap photos per pass (smoke test)")
    parser.add_argument("--json", type=Path, default=None, help="write results JSON here")
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    photos = dataset.photos[: args.limit] if args.limit else dataset.photos
    rec_photos = dataset.reference_photos() + [g for p in dataset.persons.values() for g in p.groups]
    if args.limit:
        rec_photos = rec_photos[: args.limit]
    print(f"dataset: {len(dataset.persons)} persons, {len(dataset.photos)} photos "
          f"(detection over {len(photos)}, recognition over {len(rec_photos)})")

    only = args.backends.split(",") if args.backends else None
    backends = build_backends(only)

    # decode cache: each image decoded once and reused across detect+analyze per backend run
    det_rows, rec_rows, out = [], [], {"detection": [], "recognition": []}

    for backend in backends:
        # --- detection pass (timed in isolation) ---
        counts, det_ms = [], 0.0
        for photo in photos:
            rgb = _safe_load(photo.path)
            if rgb is None:
                continue
            t = time.perf_counter()
            boxes = backend.detect(rgb)
            det_ms += (time.perf_counter() - t) * 1000
            counts.append((len(boxes), photo.expected_faces))
        det = detection_result(backend.name, counts, det_ms)
        det_rows.append(det)
        out["detection"].append(vars(det))

        # --- recognition pass (only if the backend produces embeddings) ---
        if backend.has_embeddings:
            faces_by_path, analyze_ms, faces_seen = {}, 0.0, 0
            for photo in rec_photos:
                if str(photo.path) in faces_by_path:
                    continue
                rgb = _safe_load(photo.path)
                if rgb is None:
                    continue
                t = time.perf_counter()
                faces = backend.analyze(rgb)
                analyze_ms += (time.perf_counter() - t) * 1000
                faces_by_path[str(photo.path)] = faces
                faces_seen += len(faces)
            rec = recognition_result(
                dataset, backend, faces_by_path, analyze_ms, faces_seen, len(faces_by_path)
            )
            rec_rows.append(rec)
            out["recognition"].append(vars(rec))

    _print_detection(det_rows)
    _print_recognition(rec_rows)

    if args.json:
        args.json.write_text(json.dumps(out, indent=2))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
