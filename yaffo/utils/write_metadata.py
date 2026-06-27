import json
import platform
import subprocess
from pathlib import Path
from typing import Optional
from PIL import Image, PngImagePlugin
import piexif
from yaffo.common import VIDEO_EXTENSIONS
from yaffo.utils.exiftool_path import get_exiftool_path, is_exiftool_available


_IS_MAC = platform.system().lower() == "darwin"
_EXIFTOOL_PATH = get_exiftool_path()
_HAS_EXIFTOOL = is_exiftool_available()


def _get_existing_person_in_image(photo_path: Path) -> list[str]:
    """Read existing PersonInImage values from the photo using exiftool."""
    if not _EXIFTOOL_PATH:
        return []

    try:
        cmd = [str(_EXIFTOOL_PATH), "-json", "-XMP:PersonInImage", str(photo_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return []

        data = json.loads(result.stdout)
        if not data:
            return []

        person_in_image = data[0].get("PersonInImage", [])
        if isinstance(person_in_image, str):
            return [person_in_image]
        elif isinstance(person_in_image, list):
            return person_in_image
        return []
    except (json.JSONDecodeError, subprocess.SubprocessError, Exception):
        return []


def _merge_unique(existing: list[str], new_values: list[str]) -> list[str]:
    """Merge two string lists case-insensitively (first casing wins), sorted. Used
    for both people names and keywords so writing never clobbers existing values."""
    merged = {value.lower(): value for value in existing}
    for value in new_values:
        merged.setdefault(value.lower(), value)
    return sorted(merged.values(), key=str.lower)


def _get_existing_keywords(photo_path: Path) -> list[str]:
    """Read existing XMP:Subject (keyword) values from the photo using exiftool."""
    if not _EXIFTOOL_PATH:
        return []
    try:
        cmd = [str(_EXIFTOOL_PATH), "-json", "-XMP:Subject", str(photo_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        if not data:
            return []
        subject = data[0].get("Subject", [])
        if isinstance(subject, str):
            return [subject]
        if isinstance(subject, list):
            return subject
        return []
    except (json.JSONDecodeError, subprocess.SubprocessError, Exception):
        return []


def _append_keyword_args(args: list[str], photo_path: Path, keywords: Optional[list[str]]) -> None:
    """Add the keyword (labels + custom tags) writes to an exiftool arg list, merged
    with any existing keywords so nothing is clobbered. Written to both XMP:Subject
    and IPTC:Keywords — the two standard multi-value keyword fields photo apps read."""
    if not keywords:
        return
    for keyword in _merge_unique(_get_existing_keywords(photo_path), keywords):
        args.append(f"-XMP:Subject={keyword}")
        args.append(f"-IPTC:Keywords={keyword}")


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
    people_names: Optional[list[str]] = None,
    keywords: Optional[list[str]] = None
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
            return _write_heic_metadata(photo_path, date_str, location_name, people_names, keywords)
        elif ext in (".jpg", ".jpeg"):
            return _write_jpeg_metadata(photo_path, date_str, location_name, people_names, keywords)
        elif ext == ".png":
            return _write_png_metadata(photo_path, date_str, location_name, people_names, keywords)
        elif ext in VIDEO_EXTENSIONS:
            return _write_video_metadata(photo_path, date_str, location_name, keywords)
        else:
            return False, f"Unsupported file format: {ext}"
    except subprocess.CalledProcessError as e:
        # e.stderr contains the actual error string outputted by exiftool
        cli_error = e.stderr.strip() if e.stderr else str(e)
        # If exiftool prints to stdout instead of stderr on failure, you can fallback:
        if not e.stderr and getattr(e, 'stdout', None):
            cli_error = e.stdout.strip()
        return False, f"Tool error: {cli_error} e: {e}"
    except Exception as e:
        return False, f"Error updating metadata: {e}"


def _write_heic_metadata(
    photo_path: Path,
    date_str: Optional[str],
    location_name: Optional[str],
    people_names: Optional[list[str]],
    keywords: Optional[list[str]] = None
) -> tuple[bool, Optional[str]]:
    if _HAS_EXIFTOOL:
        args = ["-overwrite_original"]
        if date_str:
            args.append(f"-DateTimeOriginal={date_str}")
        if location_name:
            args.append(f"-XMP:Location={location_name}")
        if people_names:
            merged_people = _merge_unique(_get_existing_person_in_image(photo_path), people_names)
            for person in merged_people:
                args.append(f"-XMP:PersonInImage={person}")
        _append_keyword_args(args, photo_path, keywords)
        args.append(str(photo_path))
        _run_exiftool(args)
        return True, None

    return False, "No tool available"


def _write_jpeg_metadata(
    photo_path: Path,
    date_str: Optional[str],
    location_name: Optional[str],
    people_names: Optional[list[str]],
    keywords: Optional[list[str]] = None
) -> tuple[bool, Optional[str]]:
    if _HAS_EXIFTOOL:
        args = ["-overwrite_original"]
        if date_str:
            args.append(f"-DateTimeOriginal={date_str}")
            args.append(f"-CreateDate={date_str}")
        if location_name:
            args.append(f"-XMP:Location={location_name}")
        if people_names:
            merged_people = _merge_unique(_get_existing_person_in_image(photo_path), people_names)
            for person in merged_people:
                args.append(f"-XMP:PersonInImage={person}")
        _append_keyword_args(args, photo_path, keywords)
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

        if keywords:
            # XPKeywords is the EXIF keyword field (Windows): UTF-16LE, null-terminated.
            kw_str = ";".join(sorted(keywords))
            exif_dict["0th"][piexif.ImageIFD.XPKeywords] = kw_str.encode("utf-16-le") + b"\x00\x00"

        exif_bytes = piexif.dump(exif_dict)
        img.save(photo_path, "jpeg", exif=exif_bytes)
        return True, None


def _write_video_metadata(
    video_path: Path,
    date_str: Optional[str],
    location_name: Optional[str],
    keywords: Optional[list[str]] = None,
) -> tuple[bool, Optional[str]]:
    """Write XMP keywords/location into an MP4/MOV/M4V container via exiftool.

    XMP is the portable container-level tag (QuickTime-native keyword and person
    fields are inconsistently read across players), so we write XMP:Subject keywords
    (labels + custom tags + the favorite marker) merged with any existing ones, plus
    XMP:Location. People (PersonInImage) are intentionally not written to video — see
    docs/development/video.md. Returns (True, None) with nothing to do when no applicable field
    is set, so an export that only requested people doesn't error per clip."""
    if not _HAS_EXIFTOOL:
        return False, "No tool available"

    args = ["-overwrite_original"]
    if date_str:
        args.append(f"-XMP:DateTimeOriginal={date_str}")
    if location_name:
        args.append(f"-XMP:Location={location_name}")
    if keywords:
        for keyword in _merge_unique(_get_existing_keywords(video_path), keywords):
            args.append(f"-XMP:Subject={keyword}")

    if len(args) == 1:  # only -overwrite_original: nothing applicable to write
        return True, None

    args.append(str(video_path))
    _run_exiftool(args)
    return True, None


def _write_png_metadata(
    photo_path: Path,
    date_str: Optional[str],
    location_name: Optional[str],
    people_names: Optional[list[str]],
    keywords: Optional[list[str]] = None
) -> tuple[bool, Optional[str]]:
    img = Image.open(photo_path)
    png_info = PngImagePlugin.PngInfo()

    for k, v in img.info.items():
        if isinstance(k, str) and isinstance(v, (str, bytes)):
            # Drop the keys we rewrite below so they don't accumulate stale values.
            if not k.startswith("Person_") and k != "Keywords":
                png_info.add_text(k, v if isinstance(v, str) else v.decode())

    if date_str:
        png_info.add_text("DateTaken", date_str)
    if location_name:
        png_info.add_text("Location", location_name)
    if people_names:
        for index, person_name in enumerate(sorted(people_names)):
            png_info.add_text(f"Person_{index}", person_name)
    if keywords:
        png_info.add_text("Keywords", ", ".join(sorted(keywords)))

    img.save(photo_path, pnginfo=png_info)
    return True, None