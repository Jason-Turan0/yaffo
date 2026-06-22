"""Tests for index_video — the Phase 1 video indexer that maps exiftool metadata
onto the same result shape index_photo returns (no poster, no faces)."""
from pathlib import Path
from unittest.mock import patch

import pytest

from yaffo.db.models import MEDIA_TYPE_VIDEO
from yaffo.utils.index_video import index_video

pytestmark = pytest.mark.unit


def _run(exif: dict | None) -> dict | None:
    with patch("yaffo.utils.index_video.get_exif_data_with_exiftool", return_value=exif):
        return index_video(Path("/library/clip.mov"), Path("/thumbs"))


def test_maps_metadata_onto_result_shape():
    result = _run({
        "QuickTime:Duration": 42.5,
        "Track1:ImageWidth": 1920,
        "Track1:ImageHeight": 1080,
        "Track1:CompressorID": "hvc1",
        "QuickTime:CreateDate": "2023:08:14 10:11:12",
        "EXIF:Make": "Apple",
        "EXIF:Model": "iPhone 14",
    })
    assert result["media_type"] == MEDIA_TYPE_VIDEO
    assert result["duration_seconds"] == 42.5
    assert result["width"] == 1920
    assert result["height"] == 1080
    assert result["video_codec"] == "hevc"  # hvc1 normalized
    assert result["date_taken"] == "2023-08-14T10:11:12"
    assert result["year"] == 2023 and result["month"] == 8
    assert result["device"] == "Apple iPhone 14"
    assert result["faces_data"] == []


def test_prefers_datetimeoriginal_over_createdate():
    result = _run({
        "EXIF:DateTimeOriginal": "2020:01:02 03:04:05",
        "QuickTime:CreateDate": "2023:08:14 10:11:12",
    })
    assert result["date_taken"] == "2020-01-02T03:04:05"


def test_falls_back_to_filename_date_when_no_metadata_date():
    with patch("yaffo.utils.index_video.get_exif_data_with_exiftool", return_value={}):
        result = index_video(Path("/library/2019-05-04 trip.mov"), Path("/thumbs"))
    assert result["year"] == 2019 and result["month"] == 5
    assert result["duration_seconds"] is None


def test_unknown_codec_passes_through_raw():
    result = _run({"Track1:CompressorID": "weird"})
    assert result["video_codec"] == "weird"


def test_returns_none_on_exiftool_failure():
    with patch("yaffo.utils.index_video.get_exif_data_with_exiftool", side_effect=RuntimeError("boom")):
        assert index_video(Path("/library/clip.mov"), Path("/thumbs")) is None
