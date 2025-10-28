import platform
import subprocess
from pathlib import Path
from typing import Optional
from PIL import Image, PngImagePlugin
import piexif
from yaffo.utils.exiftool_path import get_exiftool_path, is_exiftool_available


_IS_MAC = platform.system().lower() == "darwin"
_EXIFTOOL_PATH = get_exiftool_path()
_HAS_EXIFTOOL = _EXIFTOOL_PATH is not None


def _run_exiftool(args: list[str]) -> subprocess.CompletedProcess:
    """Run exiftool with the bundled or system binary."""
    if not _EXIFTOOL_PATH:
        raise FileNotFoundError("exiftool not available")

    cmd = [str(_EXIFTOOL_PATH)] + args
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def write_photo_metadata(
    photo_path: Path,
    date_taken: Optional[str] = None,
    location_name: Optional[str] = None,
    people_names: Optional[list[str]] = None
) -> tuple[bool, Optional[str]]:
    if not photo_path.exists():
        return False, f"File not found: {photo_path}"

    ext = photo_path.suffix.lower()

    date_str = None
    if date_taken:
        date_str = date_taken
        if len(date_str) == 10:
            date_str += " 00:00:00"

    try:
        if ext in (".heic", ".heif"):
            return _write_heic_metadata(photo_path, date_str, location_name, people_names)
        elif ext in (".jpg", ".jpeg"):
            return _write_jpeg_metadata(photo_path, date_str, location_name, people_names)
        elif ext == ".png":
            return _write_png_metadata(photo_path, date_str, location_name, people_names)
        else:
            return False, f"Unsupported file format: {ext}"
    except subprocess.CalledProcessError as e:
        return False, f"Tool error: {e}"
    except Exception as e:
        return False, f"Error updating metadata: {e}"


def _write_heic_metadata(
    photo_path: Path,
    date_str: Optional[str],
    location_name: Optional[str],
    people_names: Optional[list[str]]
) -> tuple[bool, Optional[str]]:
    if _HAS_EXIFTOOL:
        args = ["-overwrite_original"]
        if date_str:
            args.append(f"-DateTimeOriginal={date_str}")
        if location_name:
            args.append(f"-XMP:Location={location_name}")
        if people_names:
            for person_name in sorted(people_names):
                args.append(f"-XMP:PersonInImage+={person_name}")
        args.append(str(photo_path))
        _run_exiftool(args)
        return True, None

    return False, "No tool available"


def _write_jpeg_metadata(
    photo_path: Path,
    date_str: Optional[str],
    location_name: Optional[str],
    people_names: Optional[list[str]]
) -> tuple[bool, Optional[str]]:
    if _HAS_EXIFTOOL:
        args = ["-overwrite_original"]
        if date_str:
            args.append(f"-DateTimeOriginal={date_str}")
            args.append(f"-CreateDate={date_str}")
        if location_name:
            args.append(f"-XMP:Location={location_name}")
        if people_names:
            for person_name in sorted(people_names):
                args.append(f"-XMP:PersonInImage+={person_name}")
        args.append(str(photo_path))
        _run_exiftool(args)
        return True, None
    else:
        img = Image.open(photo_path)
        exif_dict = piexif.load(img.info.get("exif", b""))

        if date_str:
            exif_dict["0th"][piexif.ImageIFD.DateTime] = date_str.encode()
            exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = date_str.encode()
            exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = date_str.encode()

        if location_name:
            exif_dict["0th"][piexif.ImageIFD.ImageDescription] = location_name.encode()

        if people_names:
            people_str = ", ".join(sorted(people_names))
            exif_dict["0th"][piexif.ImageIFD.Artist] = people_str.encode()

        exif_bytes = piexif.dump(exif_dict)
        img.save(photo_path, "jpeg", exif=exif_bytes)
        return True, None


def _write_png_metadata(
    photo_path: Path,
    date_str: Optional[str],
    location_name: Optional[str],
    people_names: Optional[list[str]]
) -> tuple[bool, Optional[str]]:
    img = Image.open(photo_path)
    png_info = PngImagePlugin.PngInfo()

    for k, v in img.info.items():
        if isinstance(k, str) and isinstance(v, (str, bytes)):
            if not k.startswith("Person_"):
                png_info.add_text(k, v if isinstance(v, str) else v.decode())

    if date_str:
        png_info.add_text("DateTaken", date_str)
    if location_name:
        png_info.add_text("Location", location_name)
    if people_names:
        for index, person_name in enumerate(sorted(people_names)):
            png_info.add_text(f"Person_{index}", person_name)

    img.save(photo_path, pnginfo=png_info)
    return True, None