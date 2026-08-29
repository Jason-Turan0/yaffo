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

This mirrors what Playwright's toHaveScreenshot does via pixelmatch: perceived YIQ
colour distance, anti-alias detection, then a flat budget of differing pixels.

Pillow and NumPy are already project dependencies, so this needs nothing new.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
from PIL import Image

# Pixelmatch's perceived-colour threshold. Playwright uses 0.2 by default; unlike a
# max-channel delta, it does not over-count small WebP and glyph-rasterization shifts.
PERCEPTUAL_THRESHOLD = 0.2
# How many differing pixels are tolerated before a shot is "changed". Rendering is
# deterministic for a fixed container and fixture, so this only needs to cover
# encoder jitter. The smallest committed regression fixture, one changed digit,
# produces 113 pixels after the perceived-colour and anti-alias filters.
MAX_DIFF_PIXELS = 100

YIQ_MAX_DELTA = 35215
ANTIALIAS_CHUNK_SIZE = 50_000
NEIGHBOURS = (
    # Match pixelmatch's x-major traversal. The order matters when two neighbours
    # have the same brightness extreme but different sibling structure.
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1), (0, 1),
    (1, -1), (1, 0), (1, 1),
)


def _brightness(image: np.ndarray) -> np.ndarray:
    pixels = image.astype(np.float64)
    return (
        pixels[..., 0] * 0.29889531
        + pixels[..., 1] * 0.58662247
        + pixels[..., 2] * 0.11448223
    )


def _has_many_siblings(
    image: np.ndarray,
    xs: np.ndarray,
    ys: np.ndarray,
) -> np.ndarray:
    """Vectorized pixelmatch sibling test for a batch of coordinates."""
    height, width, _ = image.shape
    center = image[ys, xs]
    equal = ((xs == 0) | (xs == width - 1) | (ys == 0) | (ys == height - 1)).astype(
        np.int8
    )
    for dx, dy in NEIGHBOURS:
        neighbour_x = xs + dx
        neighbour_y = ys + dy
        valid = (
            (neighbour_x >= 0) & (neighbour_x < width)
            & (neighbour_y >= 0) & (neighbour_y < height)
        )
        indexes = np.nonzero(valid)[0]
        if indexes.size:
            equal[indexes] += np.all(
                center[indexes] == image[neighbour_y[indexes], neighbour_x[indexes]],
                axis=1,
            )
    return equal > 2


def _antialiased(
    image: np.ndarray,
    other: np.ndarray,
    brightness: np.ndarray,
    xs: np.ndarray,
    ys: np.ndarray,
) -> np.ndarray:
    """Vectorized pixelmatch intensity-slope test for candidate edge pixels."""
    height, width, _ = image.shape
    center = image[ys, xs]
    center_brightness = brightness[ys, xs]
    equal = ((xs == 0) | (xs == width - 1) | (ys == 0) | (ys == height - 1)).astype(
        np.int8
    )
    darkest = np.zeros(xs.shape, dtype=np.float64)
    brightest = np.zeros(xs.shape, dtype=np.float64)
    darkest_x, darkest_y = xs.copy(), ys.copy()
    brightest_x, brightest_y = xs.copy(), ys.copy()

    for dx, dy in NEIGHBOURS:
        neighbour_x = xs + dx
        neighbour_y = ys + dy
        valid = (
            (neighbour_x >= 0) & (neighbour_x < width)
            & (neighbour_y >= 0) & (neighbour_y < height)
        )
        indexes = np.nonzero(valid)[0]
        if not indexes.size:
            continue
        sibling_x = neighbour_x[indexes]
        sibling_y = neighbour_y[indexes]
        deltas = center_brightness[indexes] - brightness[sibling_y, sibling_x]
        equal[indexes] += np.all(center[indexes] == image[sibling_y, sibling_x], axis=1)

        lower = indexes[deltas < darkest[indexes]]
        if lower.size:
            darkest[lower] = center_brightness[lower] - brightness[
                neighbour_y[lower], neighbour_x[lower]
            ]
            darkest_x[lower], darkest_y[lower] = neighbour_x[lower], neighbour_y[lower]
        higher = indexes[deltas > brightest[indexes]]
        if higher.size:
            brightest[higher] = center_brightness[higher] - brightness[
                neighbour_y[higher], neighbour_x[higher]
            ]
            brightest_x[higher], brightest_y[higher] = neighbour_x[higher], neighbour_y[higher]

    possible = (equal <= 2) & (darkest != 0) & (brightest != 0)
    result = np.zeros(xs.shape, dtype=bool)
    indexes = np.nonzero(possible)[0]
    if not indexes.size:
        return result
    dark_in_both = _has_many_siblings(
        image, darkest_x[indexes], darkest_y[indexes]
    ) & _has_many_siblings(other, darkest_x[indexes], darkest_y[indexes])
    bright_in_both = _has_many_siblings(
        image, brightest_x[indexes], brightest_y[indexes]
    ) & _has_many_siblings(other, brightest_x[indexes], brightest_y[indexes])
    result[indexes] = dark_in_both | bright_in_both
    return result


def _perceived_delta(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Squared YIQ distance used by the pixelmatch version bundled with Playwright."""
    a = first.astype(np.float64)
    b = second.astype(np.float64)
    red = a[..., 0] - b[..., 0]
    green = a[..., 1] - b[..., 1]
    blue = a[..., 2] - b[..., 2]
    y = red * 0.29889531 + green * 0.58662247 + blue * 0.11448223
    i = red * 0.59597799 - green * 0.27417610 - blue * 0.32180189
    q = red * 0.21147017 - green * 0.52261711 + blue * 0.31114694
    return 0.5053 * y * y + 0.299 * i * i + 0.1957 * q * q


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

    delta = np.zeros((height, width), dtype=np.float64)
    delta[overlap] = _perceived_delta(
        baseline_canvas[overlap], candidate_canvas[overlap]
    )
    mask = delta > YIQ_MAX_DELTA * PERCEPTUAL_THRESHOLD * PERCEPTUAL_THRESHOLD
    mask[baseline_present ^ candidate_present] = True
    _zero_ignored(mask, json.loads(args.ignore))

    # Pixelmatch deliberately excludes pixels that its intensity-slope detector
    # identifies as anti-aliased edges. Only inspect threshold candidates; in a normal
    # screenshot comparison this is hundreds of pixels rather than millions.
    coordinates = np.argwhere(mask & overlap)
    if len(coordinates):
        baseline_brightness = _brightness(baseline_canvas)
        candidate_brightness = _brightness(candidate_canvas)
        for start in range(0, len(coordinates), ANTIALIAS_CHUNK_SIZE):
            chunk = coordinates[start:start + ANTIALIAS_CHUNK_SIZE]
            ys, xs = chunk[:, 0], chunk[:, 1]
            antialias = _antialiased(
                baseline_canvas, candidate_canvas, baseline_brightness, xs, ys
            ) | _antialiased(
                candidate_canvas, baseline_canvas, candidate_brightness, xs, ys
            )
            mask[ys[antialias], xs[antialias]] = False

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
