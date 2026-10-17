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

Pixelmatch compares pixels at the same coordinates, so content that only moved reads
as a wall of difference: a navbar 36 px shorter repaints every pixel below it. Before
the pixel test, shots of equal width are therefore aligned row by row, the way `git
diff` aligns lines (Myers' algorithm over pixel rows). The pixel test then runs on
the baseline re-laid onto the candidate's rows, and what the alignment could not
match is reported as named bands: rows inserted, removed, or replaced. That yields a
`change` kind alongside the status:

    unchanged        nothing moved and the pixel test passes
    layout-only      identical content; only featureless rows added or removed
    content-added    every baseline row survives intact; the candidate shows more
    content-removed  every candidate row was in the baseline; some baseline rows are gone
    changed          content differs

Rows are only half of it: a table whose columns widened, or a button that moved
down inside its header while the rest of the header stayed, leaves rows that match
in place yet still differ. Those stretches are aligned again along the other axis,
which is the same row alignment applied to the transposed strip, and the result is
kept only when it explains more of the difference. Nesting goes rows, then
columns, then rows within those columns.

When nothing moved, the alignment is the identity and the result is exactly the plain
pixel comparison. A width change is not aligned; it falls back to top-left alignment
and is always "changed".

Pillow and NumPy are already project dependencies, so this needs nothing new.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
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

# Row alignment. Each row is summarised as the mean brightness of ROW_BINS column
# bins. Rows whose summaries are within ROW_TOLERANCE are the same row outright, and
# rows further apart than ROW_MAYBE_TOLERANCE never are. Rows in between get the
# perceived-colour test above, and match when at most ROW_PIXEL_BUDGET pixels exceed
# its threshold. Lossy map tiles put up to 60 such pixels on a row that the anti-alias
# filter later clears, so alignment is deliberately lenient: it only has to find where
# content moved. The strict pixel test on the aligned image decides what changed.
ROW_BINS = 64
ROW_TOLERANCE = 3.0
ROW_MAYBE_TOLERANCE = 80.0
ROW_PIXEL_BUDGET = 64
# Past this many inserted plus deleted rows the shots are simply different.
MAX_ROW_EDITS = 4000
# Only matched runs at least this long are trusted from the strict signature pass.
# Shorter runs between edits are usually noise rows matching by coincidence, so the
# stretch between two anchors is re-aligned with the perceived-colour test.
ANCHOR_ROWS = 16
# Residual this close to an edit is reported but not budgeted: lossy WebP re-encodes
# the macroblocks a seam straddles differently, so rows hugging it can differ a
# little even when the content is identical.
SEAM_ROWS = 4
# Realignment along the other axis: depth 0 aligns rows, 1 columns within a strip,
# 2 rows within those columns. Residual lines closer than STRIP_GAP share one strip.
MAX_DEPTH = 2
STRIP_GAP = 16
STRIP_PAD = 4
# Nested lines are short (a strip's height, a column's), so their per-line pixel
# budget is a fraction of the line rather than ROW_PIXEL_BUDGET, which would let a
# 20-pixel column match almost anything.
NESTED_LINE_BUDGET = 0.02
# Edit spans tried in place along the other axis, per block, to bound the work.
MAX_SPAN_TRIALS = 24

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


# --- Row alignment -----------------------------------------------------------------


def _bin_edges(width: int) -> np.ndarray:
    # Never more bins than columns: an empty bin would divide by zero.
    return np.linspace(0, width, min(ROW_BINS, width) + 1).astype(int)


def _row_signatures(image: np.ndarray) -> np.ndarray:
    brightness = _brightness(image)
    edges = _bin_edges(image.shape[1])
    return np.add.reduceat(brightness, edges[:-1], axis=1) / np.diff(edges)


def _ignored_pixels(shape: tuple[int, ...], regions: list[dict]) -> np.ndarray:
    mask = np.ones(shape[:2], dtype=np.float64)
    _zero_ignored(mask, regions)
    return mask == 0


def _ignored_bins(ignored: np.ndarray) -> np.ndarray:
    edges = _bin_edges(ignored.shape[1])
    return np.logical_or.reduceat(ignored, edges[:-1], axis=1)


class _Diagonals:
    """Lazily computed row-match runs along each diagonal k = x - y of the edit graph.

    run(k, x) is how many consecutive rows match starting at baseline row x and
    candidate row x - k. Myers only visits O(D) diagonals and each is one vectorised
    comparison, so following a snake never loops in Python per row.
    """

    def __init__(self, signatures_a, signatures_b, pixels_a=None, pixels_b=None, ignored_b=None,
                 pixel_budget: int = ROW_PIXEL_BUDGET):
        self.a, self.b = signatures_a, signatures_b
        self.pixel_budget = pixel_budget
        self.pixels_a, self.pixels_b = pixels_a, pixels_b
        # Candidate pixels excluded from comparison, and the signature bins they touch.
        self.ignored_b = ignored_b
        self.ignored_bins_b = None if ignored_b is None else _ignored_bins(ignored_b)
        self.cache: dict[int, tuple[int, np.ndarray]] = {}

    def _match(self, xs: np.ndarray, k: int) -> np.ndarray:
        difference = np.abs(self.a[xs] - self.b[xs - k])
        if self.ignored_bins_b is not None:
            difference[self.ignored_bins_b[xs - k]] = 0
        spread = difference.max(axis=1)
        match = spread <= ROW_TOLERANCE
        if self.pixels_a is None:
            return match
        maybe = np.nonzero(~match & (spread <= ROW_MAYBE_TOLERANCE))[0]
        threshold = YIQ_MAX_DELTA * PERCEPTUAL_THRESHOLD * PERCEPTUAL_THRESHOLD
        for start in range(0, len(maybe), 256):
            chunk = maybe[start:start + 256]
            rows = xs[chunk]
            over = _perceived_delta(self.pixels_a[rows], self.pixels_b[rows - k]) > threshold
            if self.ignored_b is not None:
                over &= ~self.ignored_b[rows - k]
            match[chunk] = over.sum(axis=1) <= self.pixel_budget
        return match

    def run(self, k: int, x: int) -> int:
        if k not in self.cache:
            x0, x1 = max(0, k), min(len(self.a), len(self.b) + k)
            runs = np.zeros(max(0, x1 - x0) + 1, dtype=np.int64)
            if x1 > x0:
                match = self._match(np.arange(x0, x1), k)
                for index in range(len(match) - 1, -1, -1):
                    runs[index] = runs[index + 1] + 1 if match[index] else 0
            self.cache[k] = (x0, runs[:-1])
        x0, runs = self.cache[k]
        index = x - x0
        return int(runs[index]) if 0 <= index < len(runs) else 0

    def same_in_place(self, length: int) -> np.ndarray:
        return np.array([self.run(0, x) > 0 for x in range(length)], dtype=bool)


def _myers(n: int, m: int, diagonals: _Diagonals):
    """Shortest edit script between n baseline and m candidate rows, as a list of
    (op, a0, a1, b0, b1) with op in equal/delete/insert/replace; None past
    MAX_ROW_EDITS."""
    frontier = {1: 0}
    trace = []
    for d in range(MAX_ROW_EDITS + 1):
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and frontier[k - 1] < frontier[k + 1]):
                x = frontier[k + 1]
            else:
                x = frontier[k - 1] + 1
            y = x - k
            if x < n and y < m:
                step = min(diagonals.run(k, x), n - x, m - y)
                x, y = x + step, y + step
            frontier[k] = x
            if x >= n and y >= m:
                trace.append(dict(frontier))
                return _backtrack(trace, n, m)
        trace.append(dict(frontier))
    return None


def _backtrack(trace, n: int, m: int):
    ops = []
    x, y = n, m
    for d in range(len(trace) - 1, -1, -1):
        k = x - y
        if d == 0:
            if x > 0:
                ops.append(("equal", 0, x, 0, y))
            break
        previous = trace[d - 1]
        down = k == -d or (k != d and previous[k - 1] < previous[k + 1])
        previous_k = k + 1 if down else k - 1
        previous_x = previous[previous_k]
        previous_y = previous_x - previous_k
        mid_x = previous_x if down else previous_x + 1
        mid_y = mid_x - k
        if x > mid_x:
            ops.append(("equal", mid_x, x, mid_y, y))
        if down:
            ops.append(("insert", previous_x, previous_x, previous_y, mid_y))
        else:
            ops.append(("delete", previous_x, mid_x, previous_y, previous_y))
        x, y = previous_x, previous_y
    ops.reverse()
    return _merge(ops)


def _merge(ops):
    """Coalesce contiguous runs of one kind; adjacent edits become one replace."""
    merged = []
    for op in ops:
        kind, a0, a1, b0, b1 = op
        if a1 == a0 and b1 == b0:
            continue
        if merged:
            previous_kind, pa0, pa1, pb0, pb1 = merged[-1]
            if pa1 == a0 and pb1 == b0 and (previous_kind == kind or "equal" not in (previous_kind, kind)):
                merged[-1] = (kind if previous_kind == kind else "replace", pa0, a1, pb0, b1)
                continue
        merged.append(op)
    return merged


def _align(baseline, candidate, ignored, pixel_budget: int = ROW_PIXEL_BUDGET):
    """A strict signature pass finds anchors; the stretches between them are
    re-aligned with the perceived-colour row test."""
    signatures_a, signatures_b = _row_signatures(baseline), _row_signatures(candidate)
    ops = _myers(len(baseline), len(candidate), _Diagonals(signatures_a, signatures_b, ignored_b=ignored))
    if ops is None:
        return [("replace", 0, len(baseline), 0, len(candidate))]
    regions = []
    for kind, a0, a1, b0, b1 in ops:
        anchor = kind == "equal" and a1 - a0 >= ANCHOR_ROWS
        if not anchor and regions and regions[-1][0] == "region":
            regions[-1] = ("region", regions[-1][1], a1, regions[-1][3], b1)
        else:
            regions.append(("equal" if anchor else "region", a0, a1, b0, b1))
    refined = []
    for kind, a0, a1, b0, b1 in regions:
        if kind == "equal":
            refined.append((kind, a0, a1, b0, b1))
            continue
        diagonals = _Diagonals(
            signatures_a[a0:a1], signatures_b[b0:b1],
            baseline[a0:a1], candidate[b0:b1], ignored[b0:b1], pixel_budget,
        )
        sub = _myers(a1 - a0, b1 - b0, diagonals)
        if sub is None:
            refined.append(("replace", a0, a1, b0, b1))
        else:
            refined += [(k, x0 + a0, x1 + a0, y0 + b0, y1 + b0) for k, x0, x1, y0, y1 in sub]
    ops = _straighten(_merge(refined), baseline, candidate, signatures_a, signatures_b, ignored, pixel_budget)
    return _slide(ops, baseline, candidate)


def _straighten(ops, baseline, candidate, signatures_a, signatures_b, ignored,
                pixel_budget: int = ROW_PIXEL_BUDGET, max_span=12):
    """Undo detours: a stretch of edits with no net shift that lines up at least as
    well in place becomes one equal run.

    Myers is indifferent between replacing a row in place and shifting a run of
    background rows by one and back; both cost two edits, and background matches at
    either offset. The alignment only has to find where content moved. Whether
    content that stayed in place changed is the pixel test's call, made with the
    anti-alias filter and budget the row test lacks.
    """
    changed = True
    while changed:
        changed = False
        for i, first in enumerate(ops):
            if first[0] == "equal":
                continue
            for j in range(i, min(len(ops), i + max_span)):
                last = ops[j]
                if last[0] == "equal":
                    continue
                a0, b0, a1, b1 = first[1], first[3], last[2], last[4]
                if a1 - a0 != b1 - b0:
                    continue
                # Rows deleted plus rows inserted: a one-row replace costs two, like a detour.
                edited = sum((o[2] - o[1]) + (o[4] - o[3]) for o in ops[i:j + 1] if o[0] != "equal")
                diagonals = _Diagonals(
                    signatures_a[a0:a1], signatures_b[b0:b1],
                    baseline[a0:a1], candidate[b0:b1], ignored[b0:b1], pixel_budget,
                )
                if (~diagonals.same_in_place(a1 - a0)).sum() * 2 <= edited:
                    ops = _merge(ops[:i] + [("equal", a0, a1, b0, b1)] + ops[j + 1:])
                    changed = True
                    break
            if changed:
                break
    return ops


def _band(image: np.ndarray) -> str:
    """"blank" when every row of a band repeats the same row: padding or stretched
    space that carries no information, whatever its colour or borders."""
    if len(image) == 0:
        return "blank"
    signatures = _row_signatures(image)
    spread = (signatures.max(axis=0) - signatures.min(axis=0)).max()
    return "blank" if spread <= 2 * ROW_TOLERANCE else "content"


def _edit_band(image: np.ndarray) -> str:
    """_band for the lines an edit added or removed, setting aside SEAM_ROWS at each
    end. An edit's edges are as uncertain as its seams: a band of blank columns can
    clip the first strokes of the text it moved, or keep the border it stopped at.
    The pixel test already sets those lines aside; classification does the same."""
    if len(image) > 2 * SEAM_ROWS:
        image = image[SEAM_ROWS:len(image) - SEAM_ROWS]
    return _band(image)


def _slide(ops, baseline, candidate):
    """Move each lone insert or delete to its most neutral equivalent position.

    The ambiguity `git diff` resolves with its slider heuristic: when the row above a
    band equals the band's last row, the band can move up a row and still describe
    the same change. Among the equivalent positions, prefer one that is blank, so
    "the empty area grew" is not reported as content.
    """
    ops = list(ops)
    for i, (kind, a0, a1, b0, b1) in enumerate(ops):
        if kind not in ("insert", "delete") or i == 0 or i == len(ops) - 1:
            continue
        if ops[i - 1][0] != "equal" or ops[i + 1][0] != "equal":
            continue
        image, low, high = (candidate, b0, b1) if kind == "insert" else (baseline, a0, a1)
        if _band(image[low:high]) == "blank":
            continue
        signatures = _row_signatures(image)

        def same(x: int, y: int) -> bool:
            return bool(np.abs(signatures[x] - signatures[y]).max() <= ROW_TOLERANCE)

        up = 0
        while up < ops[i - 1][2] - ops[i - 1][1] and same(low - up - 1, high - up - 1):
            up += 1
        down = 0
        while down < ops[i + 1][2] - ops[i + 1][1] and same(low + down, high + down):
            down += 1
        for shift in sorted(range(-up, down + 1), key=abs):
            if shift and _band(image[low + shift:high + shift]) == "blank":
                _, pa0, pa1, pb0, pb1 = ops[i - 1]
                _, na0, na1, nb0, nb1 = ops[i + 1]
                ops[i - 1] = ("equal", pa0, pa1 + shift, pb0, pb1 + shift)
                ops[i] = (kind, a0 + shift, a1 + shift, b0 + shift, b1 + shift)
                ops[i + 1] = ("equal", na0 + shift, na1, nb0 + shift, nb1)
                break
    return _merge(ops)


def _mask(baseline: np.ndarray, candidate: np.ndarray, regions: list[dict]) -> np.ndarray:
    """Pixelmatch's verdict for two same-sized images: perceived delta above the
    threshold, ignore regions blanked, anti-aliased edges excluded."""
    return _mask_ignoring(baseline, candidate, _ignored_pixels(candidate.shape, regions))


def _mask_ignoring(baseline: np.ndarray, candidate: np.ndarray, ignored: np.ndarray) -> np.ndarray:
    """_mask with the excluded pixels given as a boolean array."""
    delta = _perceived_delta(baseline, candidate)
    mask = (delta > YIQ_MAX_DELTA * PERCEPTUAL_THRESHOLD * PERCEPTUAL_THRESHOLD) & ~ignored
    # Only inspect threshold candidates; in a normal screenshot comparison this is
    # hundreds of pixels rather than millions.
    coordinates = np.argwhere(mask)
    if len(coordinates):
        baseline_brightness = _brightness(baseline)
        candidate_brightness = _brightness(candidate)
        for start in range(0, len(coordinates), ANTIALIAS_CHUNK_SIZE):
            chunk = coordinates[start:start + ANTIALIAS_CHUNK_SIZE]
            ys, xs = chunk[:, 0], chunk[:, 1]
            antialias = _antialiased(
                baseline, candidate, baseline_brightness, xs, ys
            ) | _antialiased(
                candidate, baseline, candidate_brightness, xs, ys
            )
            mask[ys[antialias], xs[antialias]] = False
    return mask


def _box(mask: np.ndarray) -> dict | None:
    if not mask.any():
        return None
    ys, xs = np.nonzero(mask)
    return {
        "x": int(xs.min()), "y": int(ys.min()),
        "width": int(xs.max() - xs.min() + 1),
        "height": int(ys.max() - ys.min() + 1),
    }


def _change_kind(edits: list[dict], over_budget: bool) -> str:
    removed = any(edit.get("removed") == "content" for edit in edits)
    added = any(edit.get("added") == "content" for edit in edits)
    if over_budget or (removed and added):
        return "changed"
    if removed:
        return "content-removed"
    if added:
        return "content-added"
    return "layout-only" if edits else "unchanged"


# --- Alignment along both axes ------------------------------------------------------


def _transpose(pixels: np.ndarray) -> np.ndarray:
    """Columns become rows, so the row alignment aligns columns."""
    return pixels.swapaxes(0, 1)


@dataclass
class _Alignment:
    """One block aligned along its rows. Strips that were realigned along the other
    axis are children, each in its own transposed orientation."""
    ops: list
    mask: np.ndarray            # residual in candidate coordinates, seams cleared
    seam_pixels: int
    content_area: int           # pixels explained as inserted or removed content
    children: list = field(default_factory=list)   # (a0, a1, b0, b1, _Alignment)
    moves: set = field(default_factory=set)        # indices of ops that pair up as a move

    @property
    def cost(self) -> int:
        """What the alignment leaves unexplained or blames on content. Blank bands
        cost nothing: space growing or shrinking is what alignment is for."""
        return int(self.mask.sum()) + self.seam_pixels + self.content_area


def _moves(ops, baseline, candidate) -> set[int]:
    """Deletions and insertions that are the same content in two places: a move.

    Myers counts edits, and content narrower than the distance it moved is cheaper to
    delete and re-insert than to shift: a "10" seventeen pixels wide that moved twenty
    costs 34 edits that way and 40 as blank space on either side. The same blind spot
    `git diff --color-moved` covers. A deleted band and an inserted band of the same
    length whose pixels pass the pixel test against each other are paired up."""
    deleted = [i for i, (kind, a0, a1, b0, b1) in enumerate(ops)
               if kind == "delete" and _edit_band(baseline[a0:a1]) == "content"]
    inserted = [i for i, (kind, a0, a1, b0, b1) in enumerate(ops)
                if kind == "insert" and _edit_band(candidate[b0:b1]) == "content"]
    moved: set[int] = set()
    for i in deleted:
        a0, a1 = ops[i][1], ops[i][2]
        for j in inserted:
            b0, b1 = ops[j][3], ops[j][4]
            if j in moved or b1 - b0 != a1 - a0:
                continue
            same = _mask_ignoring(baseline[a0:a1], candidate[b0:b1], np.zeros((b1 - b0, candidate.shape[1]), bool))
            if int(same.sum()) <= MAX_DIFF_PIXELS // 4:
                moved.update((i, j))
                break
    return moved


def _content_area(ops, baseline, candidate, moves: set[int] = frozenset()) -> int:
    line = candidate.shape[1]
    area = 0
    for index, (kind, a0, a1, b0, b1) in enumerate(ops):
        if kind == "equal" or index in moves:
            continue
        if a1 > a0 and _edit_band(baseline[a0:a1]) == "content":
            area += (a1 - a0) * line
        if b1 > b0 and _edit_band(candidate[b0:b1]) == "content":
            area += (b1 - b0) * line
    return area


def _finish(ops, children, baseline, candidate, ignored) -> _Alignment:
    """Residual for an edit script: the baseline re-laid onto the candidate's rows,
    rows the script could not match copying the candidate, children's own residual
    in their strips, and rows hugging an edit seam set aside."""
    aligned = candidate.copy()
    for kind, a0, a1, b0, b1 in ops:
        if kind == "equal":
            aligned[b0:b1] = baseline[a0:a1]
    mask = _mask_ignoring(aligned, candidate, ignored)
    for a0, a1, b0, b1, child in children:
        mask[b0:b1] = _transpose(child.mask)
    near_seam = np.zeros(len(candidate), dtype=bool)
    for kind, a0, a1, b0, b1 in ops:
        if kind != "equal":
            for edge in {b0, b1}:
                near_seam[max(0, edge - SEAM_ROWS):edge + SEAM_ROWS] = True
    seam_pixels = int(mask[near_seam].sum()) + sum(child.seam_pixels for *_, child in children)
    mask[near_seam] = False
    moves = _moves(ops, baseline, candidate)
    content = _content_area(ops, baseline, candidate, moves) + sum(child.content_area for *_, child in children)
    return _Alignment(ops, mask, seam_pixels, content, list(children), moves)


def _strips(dirty: np.ndarray) -> list[tuple[int, int]]:
    """Clusters of residual lines, merged across small gaps and padded."""
    lines = np.nonzero(dirty)[0]
    strips: list[list[int]] = []
    for line in lines:
        if strips and line - strips[-1][1] <= STRIP_GAP:
            strips[-1][1] = int(line) + 1
        else:
            strips.append([int(line), int(line) + 1])
    return [(max(0, lo - STRIP_PAD), min(len(dirty), hi + STRIP_PAD)) for lo, hi in strips]


def _explains_more(child: _Alignment, current: int) -> bool:
    """A realignment replaces what rows found only when it at least halves the cost.
    Cutting a strip shifts the anti-alias test at its edges by a pixel or two, which
    is not an explanation of anything."""
    return child.cost * 2 <= current


def _other_axis(baseline, candidate, ignored, depth) -> _Alignment:
    return _align_block(_transpose(baseline), _transpose(candidate), _transpose(ignored), depth + 1)


def _align_block(baseline, candidate, ignored, depth: int = 0) -> _Alignment:
    """Align the rows of two equal-width blocks, then give the stretches rows could
    not explain a chance along the other axis.

    Two kinds of stretch qualify. Edits with no net shift, where rows were replaced
    in place: a button that moved down inside its header changes every row it spans
    without moving the rows around it. And equal runs that still differ: a table
    whose columns widened matches row for row yet repaints every cell. Either is
    realigned along the other axis and kept only if that lowers the cost.
    """
    budget = ROW_PIXEL_BUDGET if depth == 0 else max(1, round(candidate.shape[1] * NESTED_LINE_BUDGET))
    ops = _align(baseline, candidate, ignored, budget)
    children: list = []
    result = _finish(ops, children, baseline, candidate, ignored)
    if depth >= MAX_DEPTH:
        return result
    messy = int(result.mask.sum()) > MAX_DIFF_PIXELS or sum(op[0] != "equal" for op in ops) > 8
    if depth == 0 and messy and len(baseline) == len(candidate):
        # Rows first assumes the movement is vertical. When a sidebar widens, every
        # row shifts sideways and the row script shreds the page into edits; aligning
        # columns first explains it as one inserted band. Keep whichever explains more.
        columns_first = _other_axis(baseline, candidate, ignored, depth)
        if _explains_more(columns_first, result.cost):
            whole = [("equal", 0, len(baseline), 0, len(candidate))]
            return _finish(whole, [(0, len(baseline), 0, len(candidate), columns_first)],
                           baseline, candidate, ignored)

    # Edits with no net shift, tried in place.
    trials = 0
    i = 0
    while i < len(ops) and trials < MAX_SPAN_TRIALS:
        if ops[i][0] == "equal":
            i += 1
            continue
        # Only a local stretch counts as replaced in place: an anchor-length equal run
        # between two edits is evidence that the rows really moved.
        span = None
        for j in range(i, min(len(ops), i + 12)):
            if ops[j][0] == "equal" and ops[j][2] - ops[j][1] >= ANCHOR_ROWS:
                break
            if ops[j][0] != "equal" and ops[j][2] - ops[i][1] == ops[j][4] - ops[i][3]:
                span = j
                break
        if span is None:
            i += 1
            continue
        a0, a1, b0, b1 = ops[i][1], ops[span][2], ops[i][3], ops[span][4]
        trials += 1
        child = _other_axis(baseline[a0:a1], candidate[b0:b1], ignored[b0:b1], depth)
        span_moves = {index - i for index in result.moves if i <= index <= span}
        current = int(result.mask[b0:b1].sum()) + _content_area(ops[i:span + 1], baseline, candidate, span_moves)
        if _explains_more(child, current):
            ops = _merge(ops[:i] + [("equal", a0, a1, b0, b1)] + ops[span + 1:])
            children.append((a0, a1, b0, b1, child))
            result = _finish(ops, children, baseline, candidate, ignored)
        i += 1

    # Equal runs that still differ, strip by strip.
    covered = np.zeros(len(candidate), dtype=bool)
    for _, _, b0, b1, _ in children:
        covered[b0:b1] = True
    for kind, a0, a1, b0, b1 in list(ops):
        if kind != "equal":
            continue
        dirty = result.mask[b0:b1].any(axis=1) & ~covered[b0:b1]
        for s0, s1 in _strips(dirty):
            current = int(result.mask[b0 + s0:b0 + s1].sum())
            if current <= MAX_DIFF_PIXELS // 4 or trials >= MAX_SPAN_TRIALS * 2:
                continue
            trials += 1
            child = _other_axis(baseline[a0 + s0:a0 + s1], candidate[b0 + s0:b0 + s1],
                                ignored[b0 + s0:b0 + s1], depth)
            if _explains_more(child, current):
                children.append((a0 + s0, a0 + s1, b0 + s0, b0 + s1, child))
                covered[b0 + s0:b0 + s1] = True
    return _finish(ops, children, baseline, candidate, ignored)


def _blocks(alignment: _Alignment, axis: str, base_origin, cand_origin, extent: int, out: list) -> None:
    """Nested alignment as flat entries in the full image's coordinates. Only edits
    and runs that moved are listed; everything else matched in place."""
    for index, (kind, a0, a1, b0, b1) in enumerate(alignment.ops):
        entry = {"axis": axis, "op": kind}
        if index in alignment.moves:
            entry["moved"] = True
        # The lines this entry aligns, in each image, and the span it covers across
        # them. The span can sit at different places in the two images when the
        # strip it belongs to moved.
        if axis == "rows":
            entry.update(baseline=[base_origin[0] + a0, base_origin[0] + a1],
                         candidate=[cand_origin[0] + b0, cand_origin[0] + b1],
                         columns=[cand_origin[1], cand_origin[1] + extent],
                         baselineColumns=[base_origin[1], base_origin[1] + extent])
        else:
            entry.update(baseline=[base_origin[1] + a0, base_origin[1] + a1],
                         candidate=[cand_origin[1] + b0, cand_origin[1] + b1],
                         rows=[cand_origin[0], cand_origin[0] + extent],
                         baselineRows=[base_origin[0], base_origin[0] + extent])
        if kind == "equal":
            if b0 == a0:
                continue
            entry["offset"] = b0 - a0
        out.append(entry)
    for a0, a1, b0, b1, child in alignment.children:
        if axis == "rows":
            child_base, child_cand, child_axis = (base_origin[0] + a0, base_origin[1]), (cand_origin[0] + b0, cand_origin[1]), "columns"
        else:
            child_base, child_cand, child_axis = (base_origin[0], base_origin[1] + a0), (cand_origin[0], cand_origin[1] + b0), "rows"
        _blocks(child, child_axis, child_base, child_cand, b1 - b0, out)


def _band_of(entry: dict, baseline: np.ndarray, candidate: np.ndarray) -> None:
    """Label a nested edit's removed and added lines blank or content."""
    if entry["axis"] == "rows":
        (c0, c1), (bc0, bc1) = entry["columns"], entry["baselineColumns"]
        base_part = baseline[entry["baseline"][0]:entry["baseline"][1], bc0:bc1]
        cand_part = candidate[entry["candidate"][0]:entry["candidate"][1], c0:c1]
    else:
        (r0, r1), (br0, br1) = entry["rows"], entry["baselineRows"]
        base_part = _transpose(baseline[br0:br1, entry["baseline"][0]:entry["baseline"][1]])
        cand_part = _transpose(candidate[r0:r1, entry["candidate"][0]:entry["candidate"][1]])
    if len(base_part):
        entry["removed"] = _edit_band(base_part)
    if len(cand_part):
        entry["added"] = _edit_band(cand_part)


def compare_aligned(baseline: np.ndarray, candidate: np.ndarray, regions: list[dict]):
    """Aligned comparison of two images of equal width.

    Returns the result fields, the residual mask in candidate coordinates, and the
    row edit script, so the caller can draw the review overlay."""
    alignment = _align_block(baseline, candidate, _ignored_pixels(candidate.shape, regions))
    ops, mask = alignment.ops, alignment.mask

    hunks = []
    for index, (kind, a0, a1, b0, b1) in enumerate(ops):
        hunk = {"op": kind, "baseline": [a0, a1], "candidate": [b0, b1]}
        if index in alignment.moves:
            hunk["moved"] = True
            hunk["removed" if kind == "delete" else "added"] = "moved"
        elif kind == "equal":
            hunk["offset"] = b0 - a0
            residual = int(mask[b0:b1].sum())
            if residual:
                hunk["residualPixels"] = residual
        else:
            if a1 > a0:
                hunk["removed"] = _edit_band(baseline[a0:a1])
            if b1 > b0:
                hunk["added"] = _edit_band(candidate[b0:b1])
        hunks.append(hunk)

    blocks: list[dict] = []
    for a0, a1, b0, b1, child in alignment.children:
        nested: list[dict] = []
        _blocks(child, "columns", (a0, 0), (b0, 0), b1 - b0, nested)
        for entry in nested:
            if entry.get("moved"):
                entry["removed" if entry["op"] == "delete" else "added"] = "moved"
            elif entry["op"] != "equal":
                _band_of(entry, baseline, candidate)
        blocks += nested

    edits = [hunk for hunk in hunks if hunk["op"] != "equal"]
    nested_edits = [entry for entry in blocks if entry["op"] != "equal"]
    residual_pixels = int(mask.sum())
    change = _change_kind(edits + nested_edits, residual_pixels > MAX_DIFF_PIXELS)
    width = candidate.shape[1]
    edited_rows = sum(
        (hunk["baseline"][1] - hunk["baseline"][0]) + (hunk["candidate"][1] - hunk["candidate"][0])
        for hunk in edits
    )
    nested_pixels = 0
    marked = mask.copy()
    for entry in nested_edits:
        lines = (entry["baseline"][1] - entry["baseline"][0]) + (entry["candidate"][1] - entry["candidate"][0])
        span = entry["columns"] if entry["axis"] == "rows" else entry["rows"]
        nested_pixels += lines * (span[1] - span[0])
        c0, c1 = entry["candidate"]
        if entry["axis"] == "rows":
            marked[min(c0, len(candidate) - 1):max(c1, c0 + 1), span[0]:span[1]] = True
        else:
            marked[span[0]:span[1], min(c0, width - 1):max(c1, c0 + 1)] = True

    # The box covers residual pixels and every edited band, in candidate coordinates.
    # A deletion has no candidate rows, so it marks the row where the band was.
    for hunk in edits:
        b0, b1 = hunk["candidate"]
        marked[min(b0, len(candidate) - 1):max(b1, b0 + 1)] = True
    fields = {
        "status": "changed" if change != "unchanged" else "unchanged",
        "change": change,
        "diffPixels": residual_pixels + width * edited_rows + nested_pixels,
        "residualPixels": residual_pixels,
        "seamPixels": alignment.seam_pixels,
        "box": _box(marked) if (edits or nested_edits or residual_pixels) else None,
        "hunks": hunks if (edits or blocks) else [],
        "blocks": blocks,
    }
    return fields, mask, ops


def _write_diff(base: np.ndarray, mask: np.ndarray, path: str) -> None:
    """Dimmed baseline with differing pixels in magenta, for review in a PR."""
    faded = (base.astype(np.float32) * 0.25 + 191).astype(np.uint8)
    faded[mask] = (255, 0, 255)
    Image.fromarray(faded).save(path)


def _write_aligned_diff(
    baseline: np.ndarray, candidate: np.ndarray, ops, mask: np.ndarray, path: str, blocks=()
) -> None:
    """Baseline left, candidate right, both dimmed. Lines only in the baseline are
    red, lines only in the candidate green, and residual pixels magenta. Bands found
    inside a strip are tinted over just that strip; moved bands are left untinted."""
    gap = 24
    height = max(len(baseline), len(candidate))
    canvas = np.full((height, baseline.shape[1] + gap + candidate.shape[1], 3), 255, dtype=np.uint8)
    left = (baseline.astype(np.float32) * 0.25 + 191).astype(np.uint8)
    right = (candidate.astype(np.float32) * 0.25 + 191).astype(np.uint8)
    for kind, a0, a1, b0, b1 in ops:
        if kind == "equal":
            continue
        left[a0:a1] = (left[a0:a1] * 0.5 + np.array([255, 60, 60]) * 0.5).astype(np.uint8)
        right[b0:b1] = (right[b0:b1] * 0.5 + np.array([60, 200, 60]) * 0.5).astype(np.uint8)
    for block in blocks:
        if block["op"] == "equal" or block.get("moved"):
            continue
        (a0, a1), (b0, b1) = block["baseline"], block["candidate"]
        if block["axis"] == "columns":
            (r0, r1), (br0, br1) = block["rows"], block["baselineRows"]
            left_box, right_box = (slice(br0, br1), slice(a0, a1)), (slice(r0, r1), slice(b0, b1))
        else:
            (c0, c1), (bc0, bc1) = block["columns"], block["baselineColumns"]
            left_box, right_box = (slice(a0, a1), slice(bc0, bc1)), (slice(b0, b1), slice(c0, c1))
        left[left_box] = (left[left_box] * 0.5 + np.array([255, 60, 60]) * 0.5).astype(np.uint8)
        right[right_box] = (right[right_box] * 0.5 + np.array([60, 200, 60]) * 0.5).astype(np.uint8)
    right[mask] = (255, 0, 255)
    canvas[:len(baseline), :baseline.shape[1]] = left
    canvas[:len(candidate), baseline.shape[1] + gap:] = right
    Image.fromarray(canvas).save(path)


def _compare_top_left(baseline: Image.Image, candidate: Image.Image, regions: list[dict], diff_out):
    """Pixel comparison with top-left alignment, for shots whose width changed. Rows
    cannot be aligned across a width change, so this is always "changed"."""
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

    mask = _mask(baseline_canvas, candidate_canvas, regions) & overlap
    mask[baseline_present ^ candidate_present] = True
    _zero_ignored(mask, regions)

    diff_pixels = int(mask.sum())
    total = int((baseline_present | candidate_present).sum())
    if diff_out:
        _write_diff(baseline_canvas, mask, diff_out)
    return {
        "status": "changed",
        "change": "changed",
        "diffPixels": diff_pixels,
        "totalPixels": total,
        "ratio": round(diff_pixels / total, 8),
        "box": _box(mask),
        "diffImage": diff_out,
        "reason": "size",
        "baselineSize": list(baseline.size),
        "candidateSize": list(candidate.size),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("--ignore", default="[]", help="JSON list of {x,y,width,height}")
    parser.add_argument("--diff-out", default=None)
    args = parser.parse_args()

    baseline = Image.open(args.baseline).convert("RGB")
    candidate = Image.open(args.candidate).convert("RGB")
    regions = json.loads(args.ignore)

    if baseline.width == candidate.width:
        baseline_pixels = np.asarray(baseline)
        candidate_pixels = np.asarray(candidate)
        fields, mask, ops = compare_aligned(baseline_pixels, candidate_pixels, regions)
        changed = fields["status"] == "changed"
        if changed and args.diff_out:
            if fields["hunks"]:
                _write_aligned_diff(baseline_pixels, candidate_pixels, ops, mask, args.diff_out, fields["blocks"])
            else:
                _write_diff(baseline_pixels, mask, args.diff_out)
        total = baseline.width * max(baseline.height, candidate.height)
        result = {
            **fields,
            "totalPixels": total,
            "ratio": round(fields["diffPixels"] / total, 8),
            "diffImage": args.diff_out if (changed and args.diff_out) else None,
        }
        if baseline.size != candidate.size:
            result.update({
                "reason": "size",
                "baselineSize": list(baseline.size),
                "candidateSize": list(candidate.size),
            })
        print(json.dumps(result))
        return 0

    result = _compare_top_left(baseline, candidate, regions, args.diff_out)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
