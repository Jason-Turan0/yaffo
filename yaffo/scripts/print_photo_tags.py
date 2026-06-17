#!/usr/bin/env python3
"""Print the metadata tags of a photo file using the bundled exiftool.

By default shows the tags the export_photo_tag automation writes (people +
location) plus the capture date and GPS lat/lon. Pass --all to dump every tag.

    python -m yaffo.scripts.print_photo_tags /path/to/photo.jpg
    python -m yaffo.scripts.print_photo_tags --all /path/to/photo.jpg
"""
import argparse
import subprocess
import sys
from pathlib import Path

from yaffo.utils.exiftool_path import get_exiftool_path

# The tags export_photo_tag writes (see utils/write_metadata.py), plus date.
_DEFAULT_TAGS = [
    "-XMP:PersonInImage",
    "-XMP:Location",
    "-DateTimeOriginal",
    "-CreateDate",
    "-GPSLatitude",
    "-GPSLongitude",
]


def print_photo_tags(photo_path: Path, show_all: bool) -> int:
    exiftool = get_exiftool_path()
    if not exiftool:
        print("exiftool not available (no bundled or system binary found).", file=sys.stderr)
        return 1
    if not photo_path.exists():
        print(f"File not found: {photo_path}", file=sys.stderr)
        return 1

    tag_args = ["-G1", "-a", "-s"] if show_all else _DEFAULT_TAGS
    # Print GPS coordinates as signed decimal degrees rather than DMS.
    tag_args = ["-c", "%.6f", *tag_args]
    result = subprocess.run(
        [str(exiftool), *tag_args, str(photo_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        return result.returncode

    output = result.stdout.strip()
    if output:
        print(output)
    else:
        print("(no matching tags — people/location have not been written to this file)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a photo file's metadata tags.")
    parser.add_argument("photo", type=Path, help="Path to the photo file")
    parser.add_argument("--all", action="store_true", help="Dump every tag, not just people/location/date")
    args = parser.parse_args()
    return print_photo_tags(args.photo, args.all)


if __name__ == "__main__":
    sys.exit(main())
