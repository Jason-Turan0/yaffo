"""One review image for a changed documentation shot.

Usage:  review_sheet.py BASELINE CANDIDATE OUT [--ignore JSON] [--name NAME]

Writes WebP when OUT ends in .webp, PNG otherwise. The capture pipeline writes one
beside every changed shot (<shot>.review.webp) and every shot whose recapture
disagreed (<shot>.repeat.review.webp), so they ship in the capture artifacts.

Everything on the sheet is derived from the comparison itself: imagediff's
alignment decides which rows and columns match, where the close-ups go, which
columns they show, and how far to magnify. Nothing is written per screenshot.

Sections, top to bottom:
  header      verdict, sizes, residual, and what the heal gate would do with it
  overview    both shots side by side, matched runs joined by ribbons
  close-ups   each cluster of edits or residual pixels, magnified, same ribbons
  pixel test  the old same-coordinates diff next to the aligned residual
  hunks       the alignment as a list
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# imagediff.py lives beside this script and is loaded by path, like the pipeline does.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import imagediff as d  # noqa: E402

SHEET_WIDTH = 1800
MARGIN = 48
RIBBON_GAP = 200            # between the two shots, room for run labels
NOTE_WIDTH = 220            # right of the new shot, room for edit labels
OVERVIEW_HEIGHT = 900       # the overview is scaled to at most this tall
CONTEXT_ROWS = 48           # rows of unchanged context around a close-up
MERGE_ROWS = 120            # edits closer than this share one close-up
MAX_CLOSEUPS = 4
MAX_ZOOM = 2.0
MIN_ZOOM = 0.6              # a close-up never shrinks below this; it crops columns instead
MIN_CROP_WIDTH = 360        # physical px; narrower activity is widened around its centre
COLUMN_PAD = 48
ACTIVE_LEVELS = 24          # a pixel this far from its row's median brightness is content
ACTIVE_SPREAD = 4.0         # or a column whose brightness varies this much down the band
MIN_LABEL_HEIGHT = 16       # runs drawn shorter than this get no label

INK, MUTED, PAPER = (28, 30, 36), (110, 114, 124), (255, 255, 255)
RULE = (214, 217, 224)
GREEN, RED, MAGENTA, AMBER = (34, 160, 84), (214, 54, 54), (230, 0, 230), (200, 120, 10)
RUN_COLOURS = [(59, 130, 246), (234, 140, 30), (140, 90, 220), (20, 150, 160), (200, 70, 140)]
VERDICT_COLOURS = {
    "unchanged": (110, 114, 124), "layout-only": (34, 160, 84), "content-added": (20, 120, 200),
    "content-removed": (200, 120, 10), "changed": (214, 54, 54),
}
FONT_CANDIDATES = {
    "sans": ["/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    "bold": ["/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    "mono": ["/System/Library/Fonts/Menlo.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"],
}


def font(size: int, kind: str = "sans") -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES[kind]:
        if Path(path).exists():
            # Helvetica.ttc keeps its bold face at index 1.
            index = 1 if kind == "bold" and path.endswith("Helvetica.ttc") else 0
            return ImageFont.truetype(path, size, index=index)
    return ImageFont.load_default(size)


def text(draw, xy, value, size=17, fill=INK, kind="sans", anchor="la"):
    draw.text(xy, value, font=font(size, kind), fill=fill, anchor=anchor)


def arrow(draw, start, end, colour=MUTED, width=3, head=11):
    draw.line((start, end), fill=colour, width=width)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    draw.polygon([end,
                  (end[0] - head * math.cos(angle - 0.45), end[1] - head * math.sin(angle - 0.45)),
                  (end[0] - head * math.cos(angle + 0.45), end[1] - head * math.sin(angle + 0.45))],
                 fill=colour)


def dim(pixels: np.ndarray, keep: float) -> np.ndarray:
    return (pixels.astype(np.float32) * keep + 255 * (1 - keep)).astype(np.uint8)


def resize(pixels: np.ndarray, scale: float) -> Image.Image:
    image = Image.fromarray(pixels)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.NEAREST if scale >= 1 else Image.LANCZOS)


def pooled(mask: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Downscale a mask so that any marked pixel survives as a marked cell."""
    return np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize(size, Image.BOX)) > 0


def stack(sections: list[Image.Image], gap: int = 28) -> Image.Image:
    height = sum(section.height for section in sections) + gap * (len(sections) - 1)
    sheet = Image.new("RGB", (SHEET_WIDTH, height), PAPER)
    y = 0
    for section in sections:
        sheet.paste(section, (0, y))
        y += section.height + gap
    return sheet


# ------------------------------------------------------------------------------------
# What to show
# ------------------------------------------------------------------------------------
def window_for(ops, c0: int, c1: int) -> tuple[int, int]:
    """Baseline rows that correspond to candidate rows [c0, c1)."""
    rows = []
    for kind, a0, a1, b0, b1 in ops:
        if kind == "equal":
            lo, hi = max(b0, c0), min(b1, c1)
            if lo < hi:
                rows += [a0 + lo - b0, a0 + hi - b0]
        elif b0 < c1 and max(b1, b0 + 1) > c0:
            rows += [a0, a1]
    return (min(rows), max(rows)) if rows else (0, 0)


def block_rows(block: dict) -> tuple[int, int]:
    """The new shot's rows an edit found inside a strip covers."""
    if block["axis"] == "columns":
        return block["rows"][0], block["rows"][1]
    return block["candidate"][0], max(block["candidate"][1], block["candidate"][0] + 1)


def block_columns(block: dict) -> tuple[int, int]:
    if block["axis"] == "rows":
        return block["columns"][0], block["columns"][1]
    return block["candidate"][0], max(block["candidate"][1], block["candidate"][0] + 1)


def regions(ops, mask: np.ndarray, height: int, blocks=()) -> list[tuple[int, int]]:
    """Candidate row ranges worth a close-up: edit bands, bands found inside strips,
    and residual pixels, merged when close, padded with context, largest first. A
    strip as tall as half the shot is already readable in the overview."""
    spans = [(b0, max(b1, b0 + 1)) for kind, a0, a1, b0, b1 in ops if kind != "equal"]
    spans += [block_rows(b) for b in blocks
              if b["op"] != "equal" and block_rows(b)[1] - block_rows(b)[0] <= height // 2]
    dirty = np.nonzero(mask.any(axis=1))[0]
    if len(dirty):
        start = prev = int(dirty[0])
        for row in dirty[1:]:
            if row - prev > MERGE_ROWS:
                spans.append((start, prev + 1))
                start = int(row)
            prev = int(row)
        spans.append((start, prev + 1))
    merged: list[list[int]] = []
    for lo, hi in sorted(spans):
        if merged and lo - merged[-1][1] <= MERGE_ROWS:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    padded = [(max(0, lo - CONTEXT_ROWS), min(height, hi + CONTEXT_ROWS)) for lo, hi in merged]
    return sorted(padded, key=lambda span: span[0] - span[1])[:MAX_CLOSEUPS]


def active_columns(pixels: np.ndarray) -> np.ndarray:
    """Columns holding content: pixels well away from their row's median, or
    columns that change down the band. The second catches faint chrome (card
    edges, tinted section backgrounds) that makes a band content without any
    single pixel standing out in its row."""
    if not len(pixels):
        return np.zeros(pixels.shape[1], dtype=bool)
    brightness = d._brightness(pixels)
    median = np.median(brightness, axis=1, keepdims=True)
    standout = (np.abs(brightness - median) > ACTIVE_LEVELS).any(axis=0)
    varies = brightness.std(axis=0) > ACTIVE_SPREAD if len(pixels) > 1 else np.zeros_like(standout)
    return standout | varies


def columns_for(ops, mask, base, cand, c0, c1, w0, w1, max_width: int, blocks=()) -> tuple[int, int]:
    width = cand.shape[1]
    active = mask[c0:c1].any(axis=0)
    # A band found inside a strip marks exactly where the change is.
    for block in blocks:
        top, bottom = block_rows(block)
        if block["op"] != "equal" and top < c1 and bottom > c0:
            left, right = block_columns(block)
            active[left:right] = True
    for kind, a0, a1, b0, b1 in ops:
        if kind == "equal":
            continue
        # A blank band repeats one row, so its columns hold borders and
        # backgrounds, not anything a reviewer needs magnified.
        if b0 < c1 and b1 > c0 and d._band(cand[b0:b1]) == "content":
            active |= active_columns(cand[max(b0, c0):min(b1, c1)])
        if a1 > a0 and a0 < w1 and a1 > w0 and d._band(base[a0:a1]) == "content":
            active |= active_columns(base[max(a0, w0):min(a1, w1)])
    if not active.any():
        # Only blank bands: they span the full width and nothing in them needs
        # magnifying, so show every column.
        return 0, width
    cols = np.nonzero(active)[0]
    x0, x1 = int(cols[0]) - COLUMN_PAD, int(cols[-1]) + 1 + COLUMN_PAD
    if x1 - x0 > max_width:
        # Too wide to show at MIN_ZOOM: keep the window of that width that holds
        # the most active columns.
        counts = np.concatenate([[0], np.cumsum(active)])
        starts = np.arange(0, width - max_width + 1)
        best = int(starts[np.argmax(counts[starts + max_width] - counts[starts])])
        return best, best + max_width
    if x1 - x0 < MIN_CROP_WIDTH:
        centre = (x0 + x1) // 2
        x0, x1 = centre - MIN_CROP_WIDTH // 2, centre + MIN_CROP_WIDTH // 2
    # Slide a window that overhangs an edge back inside, keeping its width.
    if x0 < 0:
        x0, x1 = 0, min(width, x1 - x0)
    if x1 > width:
        x0, x1 = max(0, x0 - (x1 - width)), width
    return x0, x1


# ------------------------------------------------------------------------------------
# Drawing
# ------------------------------------------------------------------------------------
def pair(base, cand, ops, mask, ignore, window_b, window_c, cols, scale, caption, blocks=()) -> Image.Image:
    """Committed rows window_b and new rows window_c, columns cols, at scale, with
    matched runs joined by ribbons and edits tinted. Bands found inside a strip are
    tinted over just that strip; moved bands stay untinted, being the same pixels."""
    (w0, w1), (c0, c1), (x0, x1) = window_b, window_c, cols
    left = resize(dim(base[w0:w1, x0:x1], 0.8), scale)
    right_pixels = dim(cand[c0:c1, x0:x1], 0.8).copy()
    right_pixels[mask[c0:c1, x0:x1]] = MAGENTA
    right = resize(right_pixels, scale)
    if scale < 1:
        # Keep residual pixels visible through downscaling.
        arr = np.asarray(right).copy()
        arr[pooled(mask[c0:c1, x0:x1], right.size)] = MAGENTA
        right = Image.fromarray(arr)

    top = 40
    x_left = MARGIN + 14
    x_right = x_left + left.width + RIBBON_GAP
    height = top + max(left.height, right.height) + 40 + 22 * sum(1 for op in ops if op[0] == "equal")
    panel = Image.new("RGB", (SHEET_WIDTH, height), PAPER)
    panel.paste(left, (x_left, top))
    panel.paste(right, (x_right, top))
    layer = Image.new("RGBA", panel.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)

    def ya(row):
        return top + (row - w0) * scale

    def yb(row):
        return top + (row - c0) * scale

    runs, edits, colour_index = [], [], 0
    for kind, a0, a1, b0, b1 in ops:
        if kind == "equal":
            colour = RUN_COLOURS[colour_index % len(RUN_COLOURS)]
            colour_index += 1
            offset = b0 - a0
            lo = max(a0, w0, c0 - offset)
            hi = min(a1, w1, c1 - offset)
            if lo >= hi:
                continue
            ld.rectangle((x_left, ya(lo), x_left + left.width, ya(hi)), fill=colour + (40,))
            ld.rectangle((x_right, yb(lo + offset), x_right + right.width, yb(hi + offset)), fill=colour + (40,))
            ld.polygon([(x_left + left.width, ya(lo)), (x_right, yb(lo + offset)),
                        (x_right, yb(hi + offset)), (x_left + left.width, ya(hi))], fill=colour + (75,))
            ld.rectangle((x_left - 12, ya(lo), x_left - 5, ya(hi)), fill=colour + (255,))
            ld.rectangle((x_right + right.width + 5, yb(lo + offset), x_right + right.width + 12,
                          yb(hi + offset)), fill=colour + (255,))
            runs.append((colour, lo, hi, offset, a0, a1))
        else:
            visible_b = b0 < c1 and b1 > c0
            visible_a = a1 > a0 and a0 < w1 and a1 > w0
            if visible_b:
                ld.rectangle((x_right, yb(max(b0, c0)), x_right + right.width, yb(min(b1, c1))), fill=GREEN + (90,))
            if visible_a:
                ld.rectangle((x_left, ya(max(a0, w0)), x_left + left.width, ya(min(a1, w1))), fill=RED + (90,))
            if visible_b or visible_a or c0 <= b0 <= c1:
                edits.append((kind, a0, a1, b0, b1))
    for block in blocks:
        if block["op"] == "equal" or block.get("moved"):
            continue
        (ba0, ba1), (bb0, bb1) = block["baseline"], block["candidate"]
        if block["axis"] == "columns":
            (r0, r1), (br0, br1) = block["rows"], block["baselineRows"]
            new_box = (bb0, r0, max(bb1, bb0 + 1), r1)
            old_box = (ba0, br0, max(ba1, ba0 + 1), br1)
        else:
            (k0, k1), (bk0, bk1) = block["columns"], block["baselineColumns"]
            new_box = (k0, bb0, k1, max(bb1, bb0 + 1))
            old_box = (bk0, ba0, bk1, max(ba1, ba0 + 1))
        # Only the side an edit has: an insertion has no committed lines and a
        # removal no new ones, and a hairline there would read as a change.
        sides = []
        if bb1 > bb0:
            sides.append((new_box, x_right, c0, GREEN, (c0, c1)))
        if ba1 > ba0:
            sides.append((old_box, x_left, w0, RED, (w0, w1)))
        for (bx0, by0, bx1, by1), origin_x, origin_y, colour, (lo, hi) in sides:
            if bx1 <= x0 or bx0 >= x1 or by1 <= lo or by0 >= hi:
                continue
            ld.rectangle((origin_x + (max(bx0, x0) - x0) * scale, top + (max(by0, lo) - origin_y) * scale,
                          origin_x + (min(bx1, x1) - x0) * scale, top + (min(by1, hi) - origin_y) * scale),
                         fill=colour + (90,))
    for box in ignore:
        bx0, by0 = box["x"] - x0, box["y"] - c0
        bx1, by1 = bx0 + box["width"], by0 + box["height"]
        if bx1 > 0 and by1 > 0 and bx0 < x1 - x0 and by0 < c1 - c0:
            ld.rectangle((x_right + max(0, bx0) * scale, top + max(0, by0) * scale,
                          x_right + min(x1 - x0, bx1) * scale, top + min(c1 - c0, by1) * scale),
                         outline=(120, 124, 134, 255), width=2)
    panel = Image.alpha_composite(panel.convert("RGBA"), layer).convert("RGB")
    draw = ImageDraw.Draw(panel)
    draw.rectangle((x_left - 1, top - 1, x_left + left.width, top + left.height), outline=RULE)
    draw.rectangle((x_right - 1, top - 1, x_right + right.width, top + right.height), outline=RULE)
    text(draw, (MARGIN, 8), caption, size=18, kind="bold")
    text(draw, (x_left, top + left.height + 8), f"committed rows {w0}–{w1 - 1}, cols {x0}–{x1 - 1}", 14, MUTED)
    text(draw, (x_right, top + right.height + 8), f"new rows {c0}–{c1 - 1}", 14, MUTED)

    mid = x_left + left.width + RIBBON_GAP / 2
    next_free = 0.0
    for colour, lo, hi, offset, a0, a1 in runs:
        y_mid = (ya(lo) + ya(hi) + yb(lo + offset) + yb(hi + offset)) / 4
        if (hi - lo) * scale < MIN_LABEL_HEIGHT and len(runs) > 1:
            continue
        # Stack labels of runs that sit close together rather than overprinting.
        y_mid = max(y_mid, next_free + 18)
        next_free = y_mid + 22
        text(draw, (mid, y_mid - 9), f"{a0}–{a1 - 1}", 15, colour, "bold", "mm")
        text(draw, (mid, y_mid + 9), f"offset {offset:+d}" if offset else "in place", 14, MUTED, anchor="mm")

    # Edit labels right of the new shot, pushed down so they never overlap. When they
    # would run past the shot, one summary label replaces them.
    note_x = x_right + right.width + 24
    next_free = top
    if top + 40 * len(edits) > top + right.height + 20 or any(
            (min(b1, c1) - max(b0, c0)) * scale < 4 for kind, a0, a1, b0, b1 in edits) and len(edits) > 1:
        added = sum(b1 - b0 for kind, a0, a1, b0, b1 in edits)
        removed = sum(a1 - a0 for kind, a0, a1, b0, b1 in edits)
        y = yb(min(max(b0, c0) for kind, a0, a1, b0, b1 in edits)) - 10
        text(draw, (note_x, y), f"{len(edits)} edits", 15, AMBER, "bold")
        text(draw, (note_x, y + 18), f"+{added} / −{removed} rows", 13, MUTED)
        text(draw, (note_x, y + 34), "see close-ups", 13, MUTED)
        edits = []
    for kind, a0, a1, b0, b1 in edits:
        y = max(next_free, yb(min(max(b0, c0), c1)) + (min(b1, c1) - max(b0, c0)) * scale / 2 - 10)
        if kind == "insert":
            band = d._band(cand[b0:b1])
            label, colour = f"+{b1 - b0} rows, {band}", GREEN
        elif kind == "delete":
            band = d._band(base[a0:a1])
            label, colour = f"−{a1 - a0} rows, {band}", RED
        else:
            label, colour = f"{a1 - a0} rows became {b1 - b0}", AMBER
        text(draw, (note_x, y), label, 15, colour, "bold")
        text(draw, (note_x, y + 18), f"new y={b0}" if kind != "delete" else f"was y={a0}", 13, MUTED)
        next_free = y + 40
    return panel


def header(name: str, fields: dict, base, cand, gate: str) -> Image.Image:
    panel = Image.new("RGB", (SHEET_WIDTH, 150), PAPER)
    draw = ImageDraw.Draw(panel)
    text(draw, (MARGIN, 24), name, 28, kind="bold")
    change = fields.get("change", "changed")
    colour = VERDICT_COLOURS.get(change, INK)
    chip = font(18, "bold").getbbox(change)
    draw.rounded_rectangle((MARGIN, 72, MARGIN + chip[2] + 28, 106), radius=8, fill=colour)
    text(draw, (MARGIN + 14, 89), change, 18, PAPER, "bold", "lm")
    facts = (f"committed {base.shape[1]}×{base.shape[0]},  new {cand.shape[1]}×{cand.shape[0]}     "
             + (f"residual {fields['residualPixels']} px (budget {d.MAX_DIFF_PIXELS})     "
                f"seam {fields.get('seamPixels', 0)} px" if "residualPixels" in fields else "rows not aligned"))
    text(draw, (MARGIN + chip[2] + 48, 80), facts, 17, MUTED)
    text(draw, (MARGIN + chip[2] + 48, 104), gate, 17, INK, "bold")
    draw.line((MARGIN, 138, SHEET_WIDTH - MARGIN, 138), fill=RULE, width=1)
    return panel


def pixel_test(base, cand, mask, regions_ignore, aligned: bool) -> Image.Image:
    height = max(len(base), len(cand))
    width = max(base.shape[1], cand.shape[1])
    a = np.full((height, width, 3), 255, np.uint8)
    b = np.full((height, width, 3), 255, np.uint8)
    a[:len(base), :base.shape[1]] = base
    b[:len(cand), :cand.shape[1]] = cand
    present_a = np.zeros((height, width), bool)
    present_b = np.zeros((height, width), bool)
    present_a[:len(base), :base.shape[1]] = True
    present_b[:len(cand), :cand.shape[1]] = True
    old = d._mask(a, b, regions_ignore) & present_a & present_b
    old |= present_a ^ present_b
    columns = [("Same coordinates (before alignment)", b, old)]
    if aligned:
        columns.append(("Aligned", b, np.pad(mask, ((0, height - len(mask)), (0, width - mask.shape[1])))))
    gap = 90
    panel_w = (SHEET_WIDTH - 2 * MARGIN - gap) // 2
    scale = min(panel_w / width, 640 / height)
    panel = Image.new("RGB", (SHEET_WIDTH, round(height * scale) + 110), PAPER)
    draw = ImageDraw.Draw(panel)
    text(draw, (MARGIN, 8), "Pixel test: magenta pixels fail pixelmatch's colour test", 18, kind="bold")
    shown_w = round(width * scale)
    for i, (name, pixels, marked) in enumerate(columns):
        x = MARGIN + i * (max(shown_w, 300) + gap)
        image = resize(dim(pixels, 0.45), scale)
        arr = np.asarray(image).copy()
        arr[pooled(marked, image.size)] = MAGENTA
        panel.paste(Image.fromarray(arr), (x, 44))
        draw.rectangle((x - 1, 43, x + image.width, 44 + image.height), outline=RULE)
        text(draw, (x, 52 + image.height), f"{name}: {int(marked.sum()):,} px", 15, MUTED)
    if len(columns) == 2:
        x = MARGIN + max(shown_w, 300) + gap / 2
        arrow(draw, (x - 20, 44 + height * scale / 2), (x + 20, 44 + height * scale / 2))
    return panel


def block_line(block: dict) -> str:
    unit, where = ("cols", "rows") if block["axis"] == "columns" else ("rows", "cols")
    span = block["rows"] if block["axis"] == "columns" else block["columns"]
    (a0, a1), (b0, b1) = block["baseline"], block["candidate"]
    within = f"within {where} {span[0]}–{span[1] - 1}"
    if block["op"] == "equal":
        return f"  moved  {unit} {a0}–{a1 - 1} by {block['offset']:+d}  {within}"
    kind = block.get("added") or block.get("removed") or ""
    return f"  {block['op']:<6} {unit} {a0}–{max(a0, a1 - 1)} → {b0}–{max(b0, b1 - 1)} ({kind})  {within}"


def hunk_list(ops, mask, blocks=()) -> Image.Image:
    lines = []
    for kind, a0, a1, b0, b1 in ops:
        if kind == "equal":
            residual = int(mask[b0:b1].sum())
            lines.append(f"equal    committed {a0:>5}–{a1 - 1:<5}  new {b0:>5}–{b1 - 1:<5}  offset {b0 - a0:+5d}"
                         + (f"  residual {residual} px" if residual else ""))
        else:
            lines.append(f"{kind:<8} committed {a0:>5}–{max(a0, a1 - 1):<5}  new {b0:>5}–{max(b0, b1 - 1):<5}  "
                         f"rows {a1 - a0}→{b1 - b0}")
    if blocks:
        lines.append("inside strips:")
        lines += [block_line(block) for block in blocks]
    shown = lines[:20] + ([f"… {len(lines) - 20} more"] if len(lines) > 20 else [])
    panel = Image.new("RGB", (SHEET_WIDTH, 60 + 24 * len(shown)), PAPER)
    draw = ImageDraw.Draw(panel)
    text(draw, (MARGIN, 8), "Alignment", 18, kind="bold")
    for i, line in enumerate(shown):
        text(draw, (MARGIN, 44 + 24 * i), line, 15, kind="mono")
    return panel


def gate_for(change: str) -> str:
    return {
        "unchanged": "No action: matches what is committed.",
        "layout-only": "Promoted without a model call, once the change reproduces on recapture.",
    }.get(change, "Sent to model triage with this alignment as evidence.")


def build(base_path: str, cand_path: str, ignore: list[dict], name: str) -> Image.Image:
    base = np.asarray(Image.open(base_path).convert("RGB"))
    cand = np.asarray(Image.open(cand_path).convert("RGB"))
    if base.shape[1] != cand.shape[1]:
        fields = {"change": "changed", "diffPixels": None}
        return stack([header(name, fields, base, cand, "Width changed: rows are not aligned; sent to model triage."),
                      pixel_test(base, cand, None, ignore, aligned=False)])

    fields, mask, ops = d.compare_aligned(base, cand, ignore)
    sections = [header(name, fields, base, cand, gate_for(fields["change"]))]

    shot_w = (SHEET_WIDTH - 2 * MARGIN - RIBBON_GAP - NOTE_WIDTH - 30) / 2
    scale = min(shot_w / cand.shape[1], OVERVIEW_HEIGHT / max(len(base), len(cand)))
    blocks = fields.get("blocks", [])
    sections.append(pair(base, cand, ops, mask, ignore, (0, len(base)), (0, len(cand)),
                         (0, cand.shape[1]), scale, "Overview: matched runs joined, new lines green, removed lines red",
                         blocks))

    closeups = regions(ops, mask, len(cand), blocks) if fields["change"] != "unchanged" else []
    for index, (c0, c1) in enumerate(sorted(closeups)):
        w0, w1 = window_for(ops, c0, c1)
        w1 = max(w1, w0 + 1)
        x0, x1 = columns_for(ops, mask, base, cand, c0, c1, w0, w1, int(shot_w / MIN_ZOOM), blocks)
        # columns_for keeps content crops narrow enough for MIN_ZOOM; a blank-only
        # crop is full width and simply shrinks to fit.
        zoom = min(MAX_ZOOM, shot_w / (x1 - x0), 700 / max(c1 - c0, w1 - w0))
        sections.append(pair(base, cand, ops, mask, ignore, (w0, w1), (c0, c1), (x0, x1), zoom,
                             f"Close-up {index + 1} of {len(closeups)}, {zoom:.2g}×", blocks))
    sections.append(pixel_test(base, cand, mask, ignore, aligned=True))
    sections.append(hunk_list(ops, mask, blocks))
    return stack(sections)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("out")
    parser.add_argument("--ignore", default="[]")
    parser.add_argument("--name", default=None)
    args = parser.parse_args()
    sheet = build(args.baseline, args.candidate, json.loads(args.ignore), args.name or Path(args.candidate).name)
    if args.out.endswith(".webp"):
        # A tenth the size of PNG for these sheets, and they are only for reading.
        sheet.save(args.out, "WEBP", quality=85, method=4)
    else:
        sheet.save(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
