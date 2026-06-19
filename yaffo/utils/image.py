import numpy as np
import pillow_heif
from pathlib import Path
from PIL.Image import Image as PIL_Image
from PIL import Image

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

def image_to_numpy(image: PIL_Image):
    return np.array(image)