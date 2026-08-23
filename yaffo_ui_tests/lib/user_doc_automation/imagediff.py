"""Per-pixel comparison of two captured shots.

Usage:  imagediff.py BASELINE CANDIDATE [--ignore JSON] [--diff-out PATH]
Prints one JSON object on stdout.

Deliberately not a perceptual hash. phash and friends reduce a 4.5 MP screenshot to
a 64-bit signature of gross structure, which is blind to exactly the changes that
matter here: a renamed button, a changed count, a reordered label. Blanking a 300x46
caption in the gallery shot leaves phash, average_hash, and dhash all at distance 0
while moving 668 pixels. Perceptual hashing is the right tool for "is this the same
photograph" (which is why duplicate detection uses it) and the wrong one for "did
this UI change".

This mirrors what Playwright's toHaveScreenshot does via pixelmatch: a per-channel
colour tolerance to absorb encoder jitter, then a flat budget of differing pixels.

Pillow and NumPy are already project dependencies, so this needs nothing new.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
from PIL import Image

# Per-channel delta a pixel must exceed to count as different. Absorbs lossy-WebP
# ringing around text without hiding a real glyph change.
COLOR_THRESHOLD = 24
# How many differing pixels are tolerated before a shot is "changed". Rendering is
# deterministic for a fixed container and fixture, so this only needs to cover
# encoder jitter — the smallest meaningful change measured is ~670 pixels.
MAX_DIFF_PIXELS = 100


def _zero_ignored(delta: np.ndarray, regions: list[dict]) -> None:
    """Blank the regions excluded from comparison. Never applied to the published
    image — only to this pixel copy (see "Non-reproducible regions" in the plan)."""
    height, width = delta.shape
    for region in regions:
        x0 = max(0, int(region.get("x", 0)))
        y0 = max(0, int(region.get("y", 0)))
        x1 = min(width, x0 + int(region.get("width", 0)))
        y1 = min(height, y0 + int(region.get("height", 0)))
        if x1 > x0 and y1 > y0:
            delta[y0:y1, x0:x1] = 0


def _write_diff(base: np.ndarray, mask: np.ndarray, path: str) -> None:
    """Dimmed baseline with differing pixels in magenta, for review in a PR."""
    faded = (base.astype(np.float32) * 0.25 + 191).astype(np.uint8)
    faded[mask] = (255, 0, 255)
    Image.fromarray(faded).save(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("--ignore", default="[]", help="JSON list of {x,y,width,height}")
    parser.add_argument("--diff-out", default=None)
    args = parser.parse_args()

    baseline = Image.open(args.baseline).convert("RGB")
    candidate = Image.open(args.candidate).convert("RGB")

    baseline_pixels = np.asarray(baseline)
    candidate_pixels = np.asarray(candidate)
    width = max(baseline.width, candidate.width)
    height = max(baseline.height, candidate.height)

    # Top-left alignment preserves the page coordinate system. White padding is only
    # the review canvas; presence masks ensure every pixel belonging to just one image
    # counts as changed even when that pixel itself is white.
    baseline_canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    candidate_canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    baseline_canvas[:baseline.height, :baseline.width] = baseline_pixels
    candidate_canvas[:candidate.height, :candidate.width] = candidate_pixels

    baseline_present = np.zeros((height, width), dtype=bool)
    candidate_present = np.zeros((height, width), dtype=bool)
    baseline_present[:baseline.height, :baseline.width] = True
    candidate_present[:candidate.height, :candidate.width] = True
    overlap = baseline_present & candidate_present

    delta = np.zeros((height, width), dtype=np.int16)
    delta[overlap] = np.abs(
        baseline_canvas[overlap].astype(np.int16)
        - candidate_canvas[overlap].astype(np.int16)
    ).max(axis=1)
    delta[baseline_present ^ candidate_present] = 255
    _zero_ignored(delta, json.loads(args.ignore))

    mask = delta > COLOR_THRESHOLD
    diff_pixels = int(mask.sum())
    total = int((baseline_present | candidate_present).sum())
    size_changed = baseline.size != candidate.size
    changed = size_changed or diff_pixels > MAX_DIFF_PIXELS

    box = None
    if diff_pixels:
        ys, xs = np.nonzero(mask)
        box = {
            "x": int(xs.min()), "y": int(ys.min()),
            "width": int(xs.max() - xs.min() + 1),
            "height": int(ys.max() - ys.min() + 1),
        }

    if changed and args.diff_out:
        _write_diff(baseline_canvas, mask, args.diff_out)

    result = {
        "status": "changed" if changed else "unchanged",
        "diffPixels": diff_pixels,
        "totalPixels": total,
        "ratio": round(diff_pixels / total, 8),
        "box": box,
        "diffImage": args.diff_out if (changed and args.diff_out) else None,
    }
    if size_changed:
        result.update({
            "reason": "size",
            "baselineSize": list(baseline.size),
            "candidateSize": list(candidate.size),
        })
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
