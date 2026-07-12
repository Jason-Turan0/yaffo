import io

import numpy as np
import pillow_heif
from pathlib import Path
from PIL.Image import Image as PIL_Image
from PIL import Image, ImageOps

def convert_heif(file_path: Path):
    heif_file = pillow_heif.read_heif(str(file_path))
    return Image.frombytes(
        heif_file.mode,
        heif_file.size,
        heif_file.data,
        "raw",
        heif_file.mode,
        heif_file.stride,
    )

def image_from_path(path: Path) -> PIL_Image:
    if path.suffix.lower() in [".heic", ".heif"]:
        try:
            image = convert_heif(path)
        except Exception:
            image = Image.open(path)
    else:
        image = Image.open(path)
    if image.mode != "RGB":
        # Normalize everything to 3-channel RGB: grayscale ("L") would otherwise
        # reach face detection as a 2-D array and break its channel indexing, and
        # RGBA/LA/P/CMYK each have their own channel count.
        image = image.convert("RGB")
    return image

EXIF_ORIENTATION_TAG = 274
ORIENTATION_UPRIGHT = 1


def exif_orientation(image: PIL_Image) -> int:
    """The image's EXIF orientation tag (1-8), defaulting to 1 (upright).

    Values 5-8 are the quarter-turns: they swap the displayed width and height.
    """
    try:
        return int(image.getexif().get(EXIF_ORIENTATION_TAG, ORIENTATION_UPRIGHT))
    except Exception:  # noqa: BLE001 - a malformed EXIF block is not worth failing an index over
        return ORIENTATION_UPRIGHT


def upright_image_from_path(path: Path) -> PIL_Image:
    """The image as a viewer sees it: EXIF orientation applied.

    Every consumer that works in *displayed* pixel coordinates must load through
    this. Face detection does: browsers rotate by EXIF on their own, so a box found
    on the raw buffer would be drawn in the wrong place (and, for a quarter-turn,
    with its width and height swapped). Loading upright puts detection, the face
    crops, and the browser in one shared coordinate space.
    """
    return ImageOps.exif_transpose(image_from_path(path))


def upright_box(
    *,
    top: int, right: int, bottom: int, left: int,
    orientation: int,
    raw_width: int, raw_height: int,
) -> tuple[int, int, int, int]:
    """Map a box found on the raw buffer into upright, as-displayed coordinates.

    Only needed for faces detected before indexing started transposing (see
    upright_image_from_path) — scripts/backfill_orientation.py rotates those in
    place. Each corner is mapped, then re-normalized: a quarter-turn (tags 5-8)
    swaps the box's width and height, so a scale factor alone can never fix it.

    Returns (top, right, bottom, left), the same order the Face columns use.
    """
    quarter_turn = orientation in (5, 6, 7, 8)
    # Displayed dimensions: a quarter-turn swaps them.
    width = raw_height if quarter_turn else raw_width
    height = raw_width if quarter_turn else raw_height

    def to_upright(x: int, y: int) -> tuple[int, int]:
        return {
            1: (x, y),
            2: (width - x, y),                # mirrored horizontally
            3: (width - x, height - y),        # rotated 180
            4: (x, height - y),                # mirrored vertically
            5: (y, x),                         # transposed
            6: (width - y, x),                 # rotated 90 CW
            7: (width - y, height - x),        # transverse
            8: (y, height - x),                # rotated 90 CCW
        }.get(orientation, (x, y))

    corners = [to_upright(left, top), to_upright(right, bottom)]
    xs = [x for x, _ in corners]
    ys = [y for _, y in corners]
    return min(ys), max(xs), max(ys), min(xs)


def image_to_numpy(image: PIL_Image):
    return np.array(image)


def preview_jpeg_bytes(path: Path, max_dimension: int, quality: int = 70) -> bytes:
    """A bandwidth-friendly JPEG preview of an image file: EXIF-rotated,
    downscaled to fit max_dimension, recompressed. Used by p2p sharing to
    send gallery thumbnails instead of originals."""
    image = image_from_path(path)
    image = ImageOps.exif_transpose(image)
    image.thumbnail((max_dimension, max_dimension))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()