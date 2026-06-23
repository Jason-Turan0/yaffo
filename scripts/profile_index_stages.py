"""Per-stage indexing profiler.

Breaks `index_photo` down into its constituent stages (exiftool metadata, image
decode, face detection+embedding, thumbnail crops) and reports where wall-clock
time goes, so optimization effort lands on the dominant stage. Runs single-process
on purpose: parallelism (the spawn worker pool) speeds the whole thing up but
doesn't change the *proportions* between stages.

Face detection + embedding is now a single fused InsightFace call (SCRFD + ArcFace),
so it's one "faces" stage rather than the old detect/encode split. It also runs one
what-if: batching exiftool (one call for all files) vs the current per-file
subprocess -- now a larger relative share since detection got ~20x faster.

Usage:
    python -m scripts.profile_index_stages "<dir>" [--limit N] [--no-experiments]
"""
import argparse
import subprocess
import tempfile
import time
from pathlib import Path

from yaffo.common import PHOTO_EXTENSIONS
from yaffo.utils.exiftool_path import get_exiftool_path
from yaffo.utils.face_analysis import detect_faces
from yaffo.utils.image import image_from_path, image_to_numpy
from yaffo.utils.index_photos import get_exif_data_with_exiftool, save_face_thumbnail
from yaffo.utils.photo_dates import get_photo_date_info

STAGES = ["exif", "decode", "faces", "thumbs"]


def _photo_files(directory: Path, limit: int | None) -> list[Path]:
    files = sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in PHOTO_EXTENSIONS and not p.name.startswith(".")
    )
    return files[:limit] if limit else files


def profile_stages(files: list[Path], thumb_dir: Path) -> tuple[dict[str, float], int, float]:
    agg = {k: 0.0 for k in STAGES}
    faces_total = 0
    wall = time.perf_counter()
    for p in files:
        s = time.perf_counter(); exif = get_exif_data_with_exiftool(p); agg["exif"] += time.perf_counter() - s
        s = time.perf_counter(); img = image_to_numpy(image_from_path(p)); agg["decode"] += time.perf_counter() - s
        get_photo_date_info(str(p), exif)  # cheap; rolled into exif's neighborhood
        s = time.perf_counter(); faces = detect_faces(img); agg["faces"] += time.perf_counter() - s
        s = time.perf_counter()
        for i, f in enumerate(faces):
            save_face_thumbnail(p, i, thumb_dir, (f.location_top, f.location_right, f.location_bottom, f.location_left))
        agg["thumbs"] += time.perf_counter() - s
        faces_total += len(faces)
    return agg, faces_total, time.perf_counter() - wall


def experiment_exiftool(files: list[Path], per_file_total: float) -> None:
    s = time.perf_counter()
    subprocess.run(
        [str(get_exiftool_path()), "-json", "-G", "-n", *[str(f) for f in files]],
        capture_output=True, text=True,
    )
    batched = time.perf_counter() - s
    speedup = per_file_total / batched if batched else float("inf")
    print(f"\nexiftool: per-file summed {per_file_total:5.1f}s   batched (one call) {batched:5.1f}s   "
          f"-> {speedup:.0f}x")


def main() -> None:
    parser = argparse.ArgumentParser(description="Per-stage indexing profiler")
    parser.add_argument("directory", help="folder of photos to profile")
    parser.add_argument("--limit", type=int, default=None, help="only the first N files")
    parser.add_argument("--no-experiments", action="store_true",
                        help="skip the exiftool what-if (stage profile only)")
    args = parser.parse_args()

    files = _photo_files(Path(args.directory), args.limit)
    if not files:
        print(f"No photos found in {args.directory}")
        return
    thumb_dir = Path(tempfile.mkdtemp(prefix="yaffo_profile_"))

    agg, faces, wall = profile_stages(files, thumb_dir)
    n = len(files)
    print(f"\n{n} photos, {faces} faces, wall {wall:.1f}s  ({wall / n * 1000:.0f} ms/photo avg)\n")
    print(f"{'stage':<10}{'total s':>9}{'ms/photo':>10}{'% wall':>8}")
    print("-" * 37)
    for k, v in sorted(agg.items(), key=lambda x: -x[1]):
        print(f"{k:<10}{v:>9.1f}{v / n * 1000:>10.0f}{v / wall * 100:>7.0f}%")

    if not args.no_experiments:
        print("\n--- what-if experiment (same files) ---")
        experiment_exiftool(files, agg["exif"])


if __name__ == "__main__":
    main()
