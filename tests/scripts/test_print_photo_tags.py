"""The tag inspector picks its default tag set from the path: photos show people +
keywords, videos show keywords only (people aren't written to video containers)."""
from pathlib import Path

import pytest

from scripts.print_photo_tags import _default_tags

pytestmark = pytest.mark.unit


def test_photo_default_includes_people_and_keywords():
    tags = _default_tags(Path("/lib/IMG_1.jpg"))
    assert "-XMP:PersonInImage" in tags
    assert "-XMP:Subject" in tags


def test_video_default_has_keywords_but_not_people():
    tags = _default_tags(Path("/lib/clip.mov"))
    assert "-XMP:Subject" in tags
    assert "-XMP:PersonInImage" not in tags


def test_video_detection_is_case_insensitive():
    assert "-XMP:PersonInImage" not in _default_tags(Path("/lib/CLIP.MP4"))
