from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from yaffo.utils.image import image_from_path, image_to_numpy

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
