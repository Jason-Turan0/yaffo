from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from yaffo.utils.image import (
    exif_orientation,
    image_from_path,
    image_to_numpy,
    upright_box,
    upright_image_from_path,
)

GRAYSCALE_JPG = Path(__file__).parent / "test_data" / "jpg" / "grayscale" / "bw_grayscale.jpg"


def test_grayscale_jpg_is_stored_as_single_channel():
    """Guard the fixture: this file must stay a true grayscale ('L') JPEG, otherwise
    the regression below isn't exercising anything."""
    assert Image.open(GRAYSCALE_JPG).mode == "L"


def test_grayscale_image_is_normalized_to_rgb():
    """A black-and-white JPEG loads as PIL mode 'L' -> a 2-D numpy array, which used
    to reach face detection and crash on its channel indexing ("too many indices for
    array: array is 2-dimensional, but 3 were indexed"). image_from_path must hand
    back 3-channel RGB so the array has a channel axis."""
    image = image_from_path(GRAYSCALE_JPG)
    assert image.mode == "RGB"

    arr = image_to_numpy(image)
    assert arr.ndim == 3
    assert arr.shape[2] == 3

    # The exact operation detect_faces performs (RGB -> BGR channel flip); on the
    # old 2-D array this raised the IndexError above.
    bgr = np.ascontiguousarray(arr[:, :, ::-1])
    assert bgr.shape == arr.shape


def _sideways_jpeg(path: Path, orientation: int, size=(40, 20)) -> Path:
    exif = Image.Exif()
    exif[274] = orientation
    Image.new("RGB", size, "white").save(path, exif=exif)
    return path


class TestUprightImageFromPath:
    def test_reports_the_orientation_tag(self, tmp_path):
        image = Image.open(_sideways_jpeg(tmp_path / "s.jpg", orientation=6))
        assert exif_orientation(image) == 6

    def test_defaults_to_upright_when_the_tag_is_absent(self, tmp_path):
        path = tmp_path / "plain.jpg"
        Image.new("RGB", (40, 20), "white").save(path)
        assert exif_orientation(Image.open(path)) == 1

    def test_quarter_turn_is_applied_on_load(self, tmp_path):
        """40x20 on disk, tagged "rotate 90 CW", is 20x40 as a viewer sees it."""
        path = _sideways_jpeg(tmp_path / "s.jpg", orientation=6)

        assert image_from_path(path).size == (40, 20)   # raw buffer
        assert upright_image_from_path(path).size == (20, 40)  # as displayed


class TestUprightBox:
    def test_upright_photo_is_left_alone(self):
        box = upright_box(top=10, right=90, bottom=80, left=20,
                          orientation=1, raw_width=200, raw_height=100)
        assert box == (10, 90, 80, 20)

    def test_quarter_turn_maps_the_box_and_swaps_its_aspect(self):
        """The real regression, with the numbers off the photo that surfaced it: a
        3264x2448 landscape buffer tagged 6 (rotate 90 CW), displayed 2448x3264. The
        face box (l=1389, t=1558, r=1684, b=1778) — 295 wide, 220 tall on the raw
        buffer — must come out 220 wide and 295 tall, over the face rather than the
        chair it used to land on."""
        top, right, bottom, left = upright_box(
            top=1558, right=1684, bottom=1778, left=1389,
            orientation=6, raw_width=3264, raw_height=2448,
        )

        assert (left, top, right, bottom) == (670, 1389, 890, 1684)
        assert (right - left, bottom - top) == (220, 295)

    def test_half_turn_mirrors_both_axes(self):
        top, right, bottom, left = upright_box(
            top=10, right=90, bottom=80, left=20,
            orientation=3, raw_width=200, raw_height=100,
        )
        assert (left, top, right, bottom) == (110, 20, 180, 90)

    def test_the_other_quarter_turn_goes_the_other_way(self):
        top, right, bottom, left = upright_box(
            top=10, right=90, bottom=80, left=20,
            orientation=8, raw_width=200, raw_height=100,
        )
        # Rotate 90 CCW: displayed 100x200, x' = y, y' = height - x.
        assert (left, top, right, bottom) == (10, 110, 80, 180)
