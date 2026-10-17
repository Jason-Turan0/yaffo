"""The user-guide automation's shot comparison.

Detection has to be sensitive enough for the smallest doc-relevant change there is —
one label's text — while absorbing lossy-encoder jitter. These tests pin both ends of
that, plus the ignore-region and reframing behaviour.

The module lives with the automation's infrastructure rather than in an importable
package, so it is loaded by path.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

pytestmark = pytest.mark.unit

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "yaffo_ui_tests" / "lib" / "user_doc_automation" / "imagediff.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("imagediff", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["imagediff"] = module
    spec.loader.exec_module(module)
    return module


imagediff = _load()


def _canvas(width=400, height=300, colour=(120, 140, 160)) -> Image.Image:
    """A flat image. Flat rather than noisy so a painted region is the only
    difference, and WebP has nothing to ring around."""
    return Image.new("RGB", (width, height), colour)


def _save(image: Image.Image, path: Path) -> Path:
    # One encode per capture, matching the pipeline. Re-encoding an already-lossy
    # WebP is second-generation and smears differences across the whole frame.
    image.save(path, "WEBP", quality=88, method=6)
    return path


def _run(baseline: Path, candidate: Path, ignore=None, diff_out: Path | None = None) -> dict:
    args = [sys.executable, str(SCRIPT), str(baseline), str(candidate)]
    if ignore is not None:
        args += ["--ignore", json.dumps(ignore)]
    if diff_out is not None:
        args += ["--diff-out", str(diff_out)]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_identical_images_are_unchanged(tmp_path):
    base = _save(_canvas(), tmp_path / "a.webp")
    other = _save(_canvas(), tmp_path / "b.webp")
    result = _run(base, other)
    assert result["status"] == "unchanged"
    assert result["diffPixels"] == 0
    assert result["box"] is None


def test_a_changed_label_is_detected(tmp_path):
    """A 300x46 caption is the smallest change the guide cares about. Perceptual
    hashes are blind to it, which is why this comparison is per-pixel."""
    base = _canvas(1400, 800)
    edited = base.copy()
    edited.paste((255, 255, 255), (660, 400, 960, 446))
    result = _run(_save(base, tmp_path / "a.webp"), _save(edited, tmp_path / "b.webp"))
    assert result["status"] == "changed"
    assert result["diffPixels"] > imagediff.MAX_DIFF_PIXELS


def test_the_reported_box_bounds_the_change(tmp_path):
    base = _canvas(600, 400)
    edited = base.copy()
    edited.paste((255, 255, 255), (100, 50, 180, 90))
    result = _run(_save(base, tmp_path / "a.webp"), _save(edited, tmp_path / "b.webp"))
    box = result["box"]
    # Encoding spreads the edge a little, so the box must contain the painted region
    # without ballooning to the whole frame.
    assert box["x"] <= 100 and box["y"] <= 50
    assert box["x"] + box["width"] >= 180
    assert box["y"] + box["height"] >= 90
    assert box["width"] < 200 and box["height"] < 120


def test_change_inside_an_ignore_region_is_suppressed(tmp_path):
    base = _canvas(600, 400)
    edited = base.copy()
    edited.paste((255, 255, 255), (100, 50, 180, 90))
    ignore = [{"x": 90, "y": 40, "width": 110, "height": 70}]
    result = _run(
        _save(base, tmp_path / "a.webp"), _save(edited, tmp_path / "b.webp"), ignore=ignore
    )
    assert result["status"] == "unchanged"
    assert result["diffPixels"] == 0


def test_change_outside_an_ignore_region_still_counts(tmp_path):
    """Ignoring one region must not blind the comparison to the rest of the shot."""
    base = _canvas(600, 400)
    edited = base.copy()
    edited.paste((255, 255, 255), (100, 50, 180, 90))
    edited.paste((0, 0, 0), (400, 200, 480, 260))
    ignore = [{"x": 90, "y": 40, "width": 110, "height": 70}]
    result = _run(
        _save(base, tmp_path / "a.webp"), _save(edited, tmp_path / "b.webp"), ignore=ignore
    )
    assert result["status"] == "changed"
    assert result["box"]["x"] >= 390


def test_colour_shift_below_the_perceived_tolerance_is_ignored(tmp_path):
    """Absorbs encoder jitter even though every raw RGB pixel differs."""
    shift = 20
    base = _canvas(300, 200, (120, 120, 120))
    nudged = _canvas(300, 200, (120 + shift, 120 + shift, 120 + shift))
    result = _run(_save(base, tmp_path / "a.webp"), _save(nudged, tmp_path / "b.webp"))
    assert result["status"] == "unchanged"


def test_a_change_within_the_pixel_budget_is_tolerated(tmp_path):
    """Fewer differing pixels than MAX_DIFF_PIXELS is noise, not a change."""
    base = _canvas(600, 400)
    edited = np.asarray(base).copy()
    edited[10, 10:10 + imagediff.MAX_DIFF_PIXELS // 2] = (255, 255, 255)
    result = _run(
        _save(base, tmp_path / "a.webp"),
        _save(Image.fromarray(edited), tmp_path / "b.webp"),
    )
    assert result["diffPixels"] <= imagediff.MAX_DIFF_PIXELS
    assert result["status"] == "unchanged"


def test_a_reframed_shot_reports_pixels_present_only_in_the_larger_image(tmp_path):
    base = _save(_canvas(600, 400), tmp_path / "a.webp")
    smaller = _save(_canvas(500, 400), tmp_path / "b.webp")
    result = _run(base, smaller)
    assert result["status"] == "changed"
    assert result["reason"] == "size"
    assert result["baselineSize"] == [600, 400]
    assert result["candidateSize"] == [500, 400]
    assert result["diffPixels"] == 100 * 400
    assert result["box"] == {"x": 500, "y": 0, "width": 100, "height": 400}


def test_diff_overlay_is_written_only_when_changed(tmp_path):
    base = _canvas(600, 400)
    same = _canvas(600, 400)
    unchanged_out = tmp_path / "unchanged.diff.png"
    _run(
        _save(base, tmp_path / "a.webp"), _save(same, tmp_path / "b.webp"),
        diff_out=unchanged_out,
    )
    assert not unchanged_out.exists()

    edited = base.copy()
    edited.paste((255, 255, 255), (100, 50, 200, 120))
    changed_out = tmp_path / "changed.diff.png"
    result = _run(
        _save(base, tmp_path / "c.webp"), _save(edited, tmp_path / "d.webp"),
        diff_out=changed_out,
    )
    assert result["diffImage"] == str(changed_out)
    assert changed_out.exists()


def test_diff_overlay_highlights_the_changed_pixels(tmp_path):
    """The overlay is the reviewer's answer to "where did it move?", so the magenta
    has to land on the change and nowhere else."""
    base = _canvas(600, 400)
    edited = base.copy()
    edited.paste((255, 255, 255), (100, 50, 200, 120))
    out = tmp_path / "diff.png"
    _run(_save(base, tmp_path / "a.webp"), _save(edited, tmp_path / "b.webp"), diff_out=out)

    overlay = np.asarray(Image.open(out).convert("RGB"))
    magenta = np.all(overlay == (255, 0, 255), axis=2)
    assert magenta[50:120, 100:200].mean() > 0.9, "change should be highlighted"
    assert magenta[300:, :].sum() == 0, "untouched areas should not be highlighted"


# --- Row alignment -------------------------------------------------------------------
#
# A flat canvas aligns trivially, so these build page-like images: blocks of
# "text" bars on a background, each block's bars at different x positions so that
# no two content rows look alike.

BACKGROUND = (245, 246, 248)


def _block(index: int, width: int = 600, height: int = 40) -> np.ndarray:
    block = np.full((height, width, 3), BACKGROUND, dtype=np.uint8)
    rng = np.random.default_rng(index)
    for line in range(2):
        top = 6 + line * 18
        x = 20
        while x < width - 60:
            word = int(rng.integers(20, 70))
            block[top:top + 10, x:x + word] = (40, 44, 52)
            x += word + int(rng.integers(8, 16))
    return block


def _blank(rows: int, width: int = 600) -> np.ndarray:
    return np.full((rows, width, 3), BACKGROUND, dtype=np.uint8)


def _page(*parts: np.ndarray) -> Image.Image:
    return Image.fromarray(np.concatenate(parts))


def _edits(result: dict) -> list[dict]:
    return [hunk for hunk in result["hunks"] if hunk["op"] != "equal"]


def _rows(span: list[int]) -> int:
    return span[1] - span[0]


# Each block opens with 6 background rows and closes with 4, so a band inserted or
# removed at a block boundary can sit anywhere in a 10-row window and describe the
# same change. Tests check its size, content, and window, not one exact row.
BOUNDARY_SLACK = 10


def test_blank_space_growing_mid_page_is_layout_only(tmp_path):
    """Content pushed down by a taller empty area is the same content, not a
    wall of changed pixels below the seam."""
    blocks = [_block(i) for i in range(8)]
    base = _page(*blocks[:4], _blank(30), *blocks[4:])
    grown = _page(*blocks[:4], _blank(30 + 36), *blocks[4:])
    result = _run(_save(base, tmp_path / "a.webp"), _save(grown, tmp_path / "b.webp"))
    assert result["status"] == "changed"
    assert result["change"] == "layout-only"
    assert result["residualPixels"] <= imagediff.MAX_DIFF_PIXELS
    [edit] = _edits(result)
    assert edit["op"] == "insert" and edit["added"] == "blank"
    assert edit["candidate"][1] - edit["candidate"][0] == 36
    assert result["hunks"][-1]["offset"] == 36


def test_a_new_block_of_content_is_content_added(tmp_path):
    blocks = [_block(i) for i in range(8)]
    base = _page(*blocks)
    grown = _page(*blocks[:3], _block(99), *blocks[3:])
    result = _run(_save(base, tmp_path / "a.webp"), _save(grown, tmp_path / "b.webp"))
    assert result["change"] == "content-added"
    [edit] = _edits(result)
    assert edit["op"] == "insert" and edit["added"] == "content"
    assert _rows(edit["candidate"]) == 40
    assert abs(edit["candidate"][0] - 120) <= BOUNDARY_SLACK


def test_a_removed_block_with_content_moving_up_is_content_removed(tmp_path):
    """The frame keeps its height: what was below the removed block moves up and
    the bottom fills with background. Only the removed block is reported."""
    blocks = [_block(i) for i in range(8)]
    base = _page(*blocks)
    shrunk = _page(*blocks[:2], *blocks[3:], _blank(40))
    result = _run(_save(base, tmp_path / "a.webp"), _save(shrunk, tmp_path / "b.webp"))
    assert result["change"] == "content-removed"
    assert result["residualPixels"] <= imagediff.MAX_DIFF_PIXELS
    [removed] = [edit for edit in _edits(result) if edit.get("removed") == "content"]
    assert _rows(removed["baseline"]) == 40
    assert abs(removed["baseline"][0] - 80) <= BOUNDARY_SLACK


def test_a_change_below_moved_content_is_still_caught(tmp_path):
    """Alignment must not launder a real edit: a changed label below the seam is
    residual on matched rows, and the result is changed."""
    blocks = [_block(i) for i in range(8)]
    base = _page(*blocks[:2], _blank(20), *blocks[2:])
    edited_block = blocks[6].copy()
    edited_block[6:16, 300:420] = (200, 40, 40)
    moved = _page(*blocks[:2], _blank(20 + 24), *blocks[2:6], edited_block, blocks[7])
    result = _run(_save(base, tmp_path / "a.webp"), _save(moved, tmp_path / "b.webp"))
    assert result["change"] == "changed"
    assert result["residualPixels"] > imagediff.MAX_DIFF_PIXELS
    # The box spans the inserted band through the edit, in candidate coordinates.
    [edit] = _edits(result)
    edited_row = 2 * 40 + 44 + 4 * 40 + 16
    assert result["box"]["y"] <= edit["candidate"][0]
    assert result["box"]["y"] + result["box"]["height"] >= edited_row


def test_identical_rows_produce_no_hunks(tmp_path):
    page = _page(*[_block(i) for i in range(6)])
    result = _run(_save(page, tmp_path / "a.webp"), _save(page, tmp_path / "b.webp"))
    assert result["change"] == "unchanged"
    assert result["hunks"] == []


def test_ignore_regions_apply_to_alignment(tmp_path):
    """A timestamp that differs inside an ignore region must not break the rows it
    sits on into a replaced band."""
    blocks = [_block(i) for i in range(6)]
    base = _page(*blocks)
    stamped = np.asarray(base).copy()
    stamped[86:96, 20:200] = (200, 40, 40)
    ignore = [{"x": 10, "y": 80, "width": 200, "height": 24}]
    result = _run(
        _save(base, tmp_path / "a.webp"), _save(Image.fromarray(stamped), tmp_path / "b.webp"),
        ignore=ignore,
    )
    assert result["change"] == "unchanged"
    assert result["hunks"] == []


def test_aligned_overlay_shows_both_shots_side_by_side(tmp_path):
    blocks = [_block(i) for i in range(6)]
    base = _page(*blocks)
    grown = _page(*blocks[:3], _block(99), *blocks[3:])
    out = tmp_path / "diff.png"
    result = _run(_save(base, tmp_path / "a.webp"), _save(grown, tmp_path / "b.webp"), diff_out=out)
    overlay = Image.open(out)
    assert overlay.size == (600 + 24 + 600, 280)
    pixels = np.asarray(overlay.convert("RGB")).astype(int)
    # Inserted candidate rows are tinted green on the right-hand side, and only those.
    [edit] = _edits(result)
    top, bottom = edit["candidate"]
    band = pixels[top:bottom, 624 + 5]
    assert (band[:, 1] > band[:, 0] + 40).all()
    above = pixels[:top, 624 + 5]
    assert not (above[:, 1] > above[:, 0] + 40).any()


def test_myers_edit_script_is_minimal():
    """The alignment's edit script tiles both sequences and is a longest common
    subsequence, checked against an exact dynamic program."""
    rng = np.random.default_rng(1)

    def lcs(a, b):
        table = np.zeros((len(a) + 1, len(b) + 1), dtype=int)
        for i in range(len(a) - 1, -1, -1):
            for j in range(len(b) - 1, -1, -1):
                table[i, j] = table[i + 1, j + 1] + 1 if a[i] == b[j] else max(table[i + 1, j], table[i, j + 1])
        return table[0, 0]

    for _ in range(500):
        a = rng.integers(0, 4, rng.integers(0, 20)).tolist()
        b = rng.integers(0, 4, rng.integers(0, 20)).tolist()
        signatures_a = np.array(a, dtype=np.float64).reshape(-1, 1) * 100
        signatures_b = np.array(b, dtype=np.float64).reshape(-1, 1) * 100
        ops = imagediff._myers(len(a), len(b), imagediff._Diagonals(signatures_a, signatures_b))
        x = y = matched = 0
        for kind, a0, a1, b0, b1 in ops:
            assert (a0, b0) == (x, y)
            if kind == "equal":
                assert a[a0:a1] == b[b0:b1]
                matched += a1 - a0
            x, y = a1, b1
        assert (x, y) == (len(a), len(b))
        assert matched == lcs(a, b)


def test_images_narrower_than_the_signature_bins_still_align(tmp_path):
    """Fewer columns than ROW_BINS once left empty bins, whose NaN means made every
    row unmatchable and turned one added row into a whole-image replace."""
    base = _save(_canvas(20, 20, (0, 0, 0)), tmp_path / "a.webp")
    taller = _save(_canvas(20, 21, (0, 0, 0)), tmp_path / "b.webp")
    result = _run(base, taller)
    assert result["change"] == "layout-only"
    assert result["diffPixels"] == 20
    assert result["box"] == {"x": 0, "y": 20, "width": 20, "height": 1}


# --- Column pass -----------------------------------------------------------------------
#
# Content that moves sideways, or moves within part of a row, leaves rows that match
# in place yet differ. These pages paste the same cell pixels at new positions, the
# whole-pixel moves the column pass can explain.

def _cell(seed: int, width: int = 90, height: int = 30) -> np.ndarray:
    return _block(seed, width=width + 80, height=height)[:, 20:20 + width]


def _grid_page(column_x: list[int], width: int = 900, rows: int = 5, override=None) -> Image.Image:
    page = np.full((60 + rows * 50, width, 3), BACKGROUND, dtype=np.uint8)
    for row in range(rows):
        for column, x in enumerate(column_x):
            seed = 100 * (override[2] if override and (row, column) == override[:2] else row) + column
            page[40 + row * 50:70 + row * 50, x:x + 90] = _cell(seed)
    return Image.fromarray(page)


def test_widened_columns_are_layout_only(tmp_path):
    base = _grid_page([20, 220, 420])
    wider = _grid_page([20, 260, 490])
    result = _run(_save(base, tmp_path / "a.webp"), _save(wider, tmp_path / "b.webp"))
    assert result["change"] == "layout-only"
    assert result["residualPixels"] <= imagediff.MAX_DIFF_PIXELS
    inserted = [b for b in result["blocks"] if b["axis"] == "columns" and b["op"] == "insert"]
    assert sorted(b["candidate"][1] - b["candidate"][0] for b in inserted) == [30, 40]
    assert all(b["added"] == "blank" for b in inserted)


def test_changed_cell_after_columns_widen_is_still_changed(tmp_path):
    """Explaining the move must not explain away an edit made alongside it."""
    base = _grid_page([20, 220, 420])
    edited = _grid_page([20, 260, 490], override=(2, 1, 9))
    result = _run(_save(base, tmp_path / "a.webp"), _save(edited, tmp_path / "b.webp"))
    assert result["change"] == "changed"


def test_element_moved_down_within_its_row_is_layout_only(tmp_path):
    """A button that moves down inside a header whose title stays put changes every
    row it spans; the other axis isolates its columns and finds a plain move."""
    def header(button_y: int) -> Image.Image:
        page = np.full((260, 900, 3), BACKGROUND, dtype=np.uint8)
        page[30:60, 20:200] = _cell(1, width=180)
        page[button_y:button_y + 30, 700:790] = _cell(2)
        page[140:240, 20:880] = _block(3, width=860, height=100)
        return Image.fromarray(page)
    result = _run(_save(header(24), tmp_path / "a.webp"), _save(header(38), tmp_path / "b.webp"))
    assert result["change"] == "layout-only"
    moved = [b for b in result["blocks"] if b["op"] == "equal"]
    assert any(b["axis"] == "rows" and b["offset"] == 14 for b in moved)


def test_widened_sidebar_is_layout_only(tmp_path):
    """Every row shifts sideways, so rows alone shred the page into edits; aligning
    columns first explains it as one band."""
    def page(sidebar: int) -> Image.Image:
        pixels = np.full((300, 900, 3), BACKGROUND, dtype=np.uint8)
        pixels[:, :sidebar] = (230, 233, 238)
        for row in range(5):
            pixels[20 + row * 55:50 + row * 55, sidebar + 30:sidebar + 30 + 400] = _cell(10 + row, width=400)
        return Image.fromarray(pixels)
    result = _run(_save(page(150), tmp_path / "a.webp"), _save(page(190), tmp_path / "b.webp"))
    assert result["change"] == "layout-only"
    assert any(b["axis"] == "columns" and b["op"] == "insert" and b["candidate"][1] - b["candidate"][0] == 40
               for b in result["blocks"])


def test_overlay_tints_column_bands_found_inside_a_strip(tmp_path):
    out = tmp_path / "diff.png"
    result = _run(_save(_grid_page([20, 220, 420]), tmp_path / "a.webp"),
                  _save(_grid_page([20, 260, 490]), tmp_path / "b.webp"), diff_out=out)
    overlay = np.asarray(Image.open(out).convert("RGB")).astype(int)
    band = next(b for b in result["blocks"] if b["axis"] == "columns" and b["op"] == "insert")
    x = 900 + 24 + (band["candidate"][0] + band["candidate"][1]) // 2
    tinted = overlay[band["rows"][0]:band["rows"][1], x]
    assert (tinted[:, 1] > tinted[:, 0] + 40).all(), "inserted columns are green on the new side"
