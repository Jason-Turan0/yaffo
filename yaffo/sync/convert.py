"""`accepts` conversion: a JPEG copy of a photo a target can't take as-is.

An image whose type isn't accepted, or that's over `max_bytes` / `max_pixels`, is
converted to a JPEG copy, downscaled to fit and stepped down in quality until it fits
`max_bytes`. The copy keeps the original's EXIF (capture date, GPS, camera) with the
orientation baked into the pixels, so the target sees what the original would have
told it. Videos are never transcoded; they're rejected as unsupported instead.
The library's original is never modified.
"""
from __future__ import annotations

import io
import math
import mimetypes
from dataclasses import dataclass
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps

from yaffo.common import MEDIA_TYPE_VIDEO, media_type_for_path
from yaffo.utils.image import ORIENTATION_UPRIGHT, EXIF_ORIENTATION_TAG, image_from_path

JPEG_MIME = "image/jpeg"
_QUALITY_STEPS = (90, 85, 80, 72, 65, 58, 50)
_MIN_SCALE = 0.1

mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("image/heif", ".heif")


@dataclass(frozen=True)
class Accepts:
    types: tuple[str, ...]
    max_bytes: int | None = None
    max_pixels: int | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> Accepts | None:
        if not data:
            return None
        return cls(types=tuple(data.get("types", ())), max_bytes=data.get("max_bytes"),
                   max_pixels=data.get("max_pixels"))


class UnsupportedFormat(Exception):
    """The item can't be sent to this target (a video it doesn't accept)."""


def mime_for(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _pixels(path: Path) -> int | None:
    try:
        with Image.open(path) as image:
            return image.width * image.height
    except Exception:  # noqa: BLE001 - HEIC without the opener registered, or unreadable
        return None


def needs_conversion(path: Path, accepts: Accepts | None) -> bool:
    if accepts is None:
        return False
    if media_type_for_path(path) == MEDIA_TYPE_VIDEO:
        if mime_for(path) not in accepts.types:
            raise UnsupportedFormat(f"{path.name}: this target only accepts {', '.join(accepts.types)}")
        return False
    if mime_for(path) not in accepts.types:
        return True
    if accepts.max_bytes and path.stat().st_size > accepts.max_bytes:
        return True
    if accepts.max_pixels:
        pixels = _pixels(path)
        return pixels is None or pixels > accepts.max_pixels
    return False


_HEIF_SUFFIXES = (".heic", ".heif")


def _upright_with_exif(source: Path) -> tuple[Image.Image, bytes | None]:
    """The photo's pixels as displayed, plus its EXIF with orientation reset to
    upright (the rotation is baked into the pixels). libheif already applies a HEIC's
    rotation while decoding, so only other formats are transposed here."""
    try:
        if source.suffix.lower() in _HEIF_SUFFIXES:
            image = image_from_path(source)
            raw = pillow_heif.open_heif(str(source)).info.get("exif")
            exif = Image.Exif()
            if raw:
                exif.load(raw)
        else:
            original = image_from_path(source)
            exif = original.getexif()
            image = ImageOps.exif_transpose(original)
    except Exception:  # noqa: BLE001 - malformed EXIF: fall back to the pixels alone
        return ImageOps.exif_transpose(image_from_path(source)), None
    if not exif:
        return image, None
    exif[EXIF_ORIENTATION_TAG] = ORIENTATION_UPRIGHT
    return image, exif.tobytes()


def _encode(image: Image.Image, exif: bytes | None, quality: int) -> bytes:
    buffer = io.BytesIO()
    kwargs = {"format": "JPEG", "quality": quality, "optimize": True}
    if exif:
        kwargs["exif"] = exif
    image.save(buffer, **kwargs)
    return buffer.getvalue()


def convert_to_jpeg(source: Path, destination: Path, accepts: Accepts) -> Path:
    """Write a JPEG copy of `source` to `destination` that fits `accepts`."""
    image, exif = _upright_with_exif(source)
    full_width = image.width
    if image.mode != "RGB":
        image = image.convert("RGB")
    if accepts.max_pixels and image.width * image.height > accepts.max_pixels:
        scale = math.sqrt(accepts.max_pixels / (image.width * image.height))
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                             Image.Resampling.LANCZOS)
    data = b""
    while True:
        for quality in _QUALITY_STEPS:
            data = _encode(image, exif, quality)
            if not accepts.max_bytes or len(data) <= accepts.max_bytes:
                destination.write_bytes(data)
                return destination
        if image.width * image.height <= 1:
            break
        image = image.resize((max(1, int(image.width * 0.8)), max(1, int(image.height * 0.8))),
                             Image.Resampling.LANCZOS)
        if image.width < full_width * _MIN_SCALE:
            break
    destination.write_bytes(data)
    return destination
