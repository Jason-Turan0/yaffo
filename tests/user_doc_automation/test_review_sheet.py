"""The review sheet the capture pipeline writes beside each changed shot.

It is a reading aid, so these tests pin what a reviewer relies on rather than pixels:
it renders for each kind of comparison, at a fixed width, as the format asked for.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

pytestmark = pytest.mark.unit

LIB = Path(__file__).resolve().parents[2] / "yaffo_ui_tests" / "lib" / "user_doc_automation"
FIXTURES = LIB / "__tests__" / "fixtures" / "imagediff"
SCRIPT = LIB / "review_sheet.py"


def _load():
    spec = importlib.util.spec_from_file_location("review_sheet", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["review_sheet"] = module
    spec.loader.exec_module(module)
    return module


review_sheet = _load()


@pytest.mark.parametrize("base, candidate", [
    ("columns-base.webp", "columns-candidate.webp"),            # bands found along columns
    ("pr63-context-base.webp", "pr63-context-candidate.webp"),  # rows inserted
    ("text-base.webp", "text-count-change.webp"),               # changed in place
    ("pr15-base.webp", "pr15-candidate.webp"),                  # unchanged
])
def test_sheet_renders_at_a_fixed_width(base, candidate):
    sheet = review_sheet.build(str(FIXTURES / base), str(FIXTURES / candidate), [], candidate)
    assert sheet.width == review_sheet.SHEET_WIDTH
    assert sheet.height > 300


def test_a_width_change_gets_a_header_and_pixel_test_only(tmp_path):
    narrow = tmp_path / "narrow.webp"
    image = Image.open(FIXTURES / "text-base.webp")
    image.crop((0, 0, image.width - 40, image.height)).save(narrow, "WEBP", quality=88)
    sheet = review_sheet.build(str(FIXTURES / "text-base.webp"), str(narrow), [], "narrow")
    assert sheet.width == review_sheet.SHEET_WIDTH


def test_the_cli_writes_webp_for_a_webp_path(tmp_path):
    out = tmp_path / "shot.review.webp"
    subprocess.run([sys.executable, str(SCRIPT), str(FIXTURES / "columns-base.webp"),
                    str(FIXTURES / "columns-candidate.webp"), str(out), "--name", "shot"], check=True)
    assert Image.open(out).format == "WEBP"
