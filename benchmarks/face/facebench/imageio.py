"""Decode images to numpy, HEIC included. Every backend gets the *same* decoded
pixels so detection/recognition differences are the library's, not the decoder's."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pillow_heif
from PIL import Image, ImageFile, ImageOps

pillow_heif.register_heif_opener()
# Real libraries contain the odd truncated/partial JPEG; tolerate them (PIL fills
# the missing tail) rather than aborting a whole benchmark run on one bad file.
ImageFile.LOAD_TRUNCATED_IMAGES = True


def load_rgb(path: Path) -> np.ndarray:
    """RGB uint8 HxWx3, with EXIF orientation applied (so sideways phone photos
    are upright before detection)."""
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return np.asarray(img)


def rgb_to_bgr(rgb: np.ndarray) -> np.ndarray:
    """OpenCV wants BGR."""
    return rgb[:, :, ::-1].copy()
