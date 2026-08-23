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


def test_colour_shift_below_the_tolerance_is_ignored(tmp_path):
    """Absorbs encoder jitter: a shift smaller than COLOR_THRESHOLD is not a change
    even though every pixel differs."""
    shift = imagediff.COLOR_THRESHOLD - 4
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


def test_a_reframed_shot_is_changed_without_pixel_maths(tmp_path):
    base = _save(_canvas(600, 400), tmp_path / "a.webp")
    smaller = _save(_canvas(500, 400), tmp_path / "b.webp")
    result = _run(base, smaller)
    assert result["status"] == "changed"
    assert result["reason"] == "size"
    assert result["diffPixels"] is None


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
