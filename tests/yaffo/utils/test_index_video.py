"""Tests for index_video — the video indexer that maps exiftool metadata onto the
same result shape index_photo returns and extracts a poster frame via ffmpeg."""
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from yaffo.db.models import MEDIA_TYPE_VIDEO
from yaffo.utils import index_video as iv
from yaffo.utils.index_video import index_video, extract_poster

pytestmark = pytest.mark.unit


def _run(exif: dict | None) -> dict | None:
    # Poster + face extraction shell out to ffmpeg/InsightFace; stub them so these
    # stay metadata-only.
    with patch("yaffo.utils.index_video.get_exif_data_with_exiftool", return_value=exif), \
         patch("yaffo.utils.index_video.extract_poster", return_value=None), \
         patch("yaffo.utils.index_video.detect_video_faces", return_value=[]):
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
    assert "poster_path" in result


def test_prefers_datetimeoriginal_over_createdate():
    result = _run({
        "EXIF:DateTimeOriginal": "2020:01:02 03:04:05",
        "QuickTime:CreateDate": "2023:08:14 10:11:12",
    })
    assert result["date_taken"] == "2020-01-02T03:04:05"


def test_falls_back_to_filename_date_when_no_metadata_date():
    with patch("yaffo.utils.index_video.get_exif_data_with_exiftool", return_value={}), \
         patch("yaffo.utils.index_video.extract_poster", return_value=None), \
         patch("yaffo.utils.index_video.detect_video_faces", return_value=[]):
        result = index_video(Path("/library/2019-05-04 trip.mov"), Path("/thumbs"))
    assert result["year"] == 2019 and result["month"] == 5
    assert result["duration_seconds"] is None


def test_unknown_codec_passes_through_raw():
    result = _run({"Track1:CompressorID": "weird"})
    assert result["video_codec"] == "weird"


def test_returns_none_on_exiftool_failure():
    with patch("yaffo.utils.index_video.get_exif_data_with_exiftool", side_effect=RuntimeError("boom")):
        assert index_video(Path("/library/clip.mov"), Path("/thumbs")) is None


class TestExtractPoster:
    def test_returns_none_when_ffmpeg_unavailable(self, tmp_path):
        with patch("yaffo.utils.index_video.get_ffmpeg_path", return_value=None):
            assert extract_poster(Path("/library/clip.mov"), tmp_path, 10.0) is None

    def test_seeks_to_middle_and_returns_poster(self, tmp_path):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            Path(cmd[cmd.index("-y") + 1]).write_bytes(b"jpeg")  # ffmpeg writes the file
            return MagicMock(returncode=0)

        with patch("yaffo.utils.index_video.get_ffmpeg_path", return_value=Path("/bin/ffmpeg")), \
             patch("yaffo.utils.index_video.subprocess.run", side_effect=fake_run):
            poster = extract_poster(Path("/library/clip.mov"), tmp_path, 42.0)

        assert poster is not None and poster.exists()
        assert poster.parent == tmp_path and poster.name.startswith("poster_")
        # middle of a 42s clip
        assert "-ss" in captured["cmd"]
        assert captured["cmd"][captured["cmd"].index("-ss") + 1] == "21.000"

    def test_falls_back_to_early_offset_without_duration(self, tmp_path):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            Path(cmd[cmd.index("-y") + 1]).write_bytes(b"jpeg")
            return MagicMock(returncode=0)

        with patch("yaffo.utils.index_video.get_ffmpeg_path", return_value=Path("/bin/ffmpeg")), \
             patch("yaffo.utils.index_video.subprocess.run", side_effect=fake_run):
            extract_poster(Path("/library/clip.mov"), tmp_path, None)

        assert captured["cmd"][captured["cmd"].index("-ss") + 1] == "1.000"

    def test_returns_none_when_ffmpeg_errors(self, tmp_path):
        with patch("yaffo.utils.index_video.get_ffmpeg_path", return_value=Path("/bin/ffmpeg")), \
             patch("yaffo.utils.index_video.subprocess.run",
                   return_value=MagicMock(returncode=1, stderr=b"bad")):
            assert extract_poster(Path("/library/clip.mov"), tmp_path, 10.0) is None

    def test_poster_name_is_stable_per_source(self, tmp_path):
        a = iv._poster_path_for(Path("/library/clip.mov"), tmp_path)
        b = iv._poster_path_for(Path("/library/clip.mov"), tmp_path)
        c = iv._poster_path_for(Path("/library/other.mov"), tmp_path)
        assert a == b and a != c


def _face(embedding, det_score=0.9, top=0):
    from yaffo.utils.face_analysis import DetectedFace
    return DetectedFace(
        location_top=top, location_right=10, location_bottom=10, location_left=0,
        embedding=np.asarray(embedding, dtype=np.float32),
        age=30.0, gender=1, det_score=det_score,
    )


class TestSampleOffsets:
    def test_no_duration_falls_back_to_single_early_frame(self):
        assert iv._sample_offsets(None) == [1.0]
        assert iv._sample_offsets(0) == [1.0]

    def test_spaces_frames_by_interval_within_bounds(self):
        offsets = iv._sample_offsets(22.0)  # 22 // 3 == 7 frames
        assert len(offsets) == 7
        assert all(0 < t < 22 for t in offsets)
        assert offsets == sorted(offsets)

    def test_caps_long_clips(self):
        assert len(iv._sample_offsets(3600.0)) == iv._FACE_SAMPLE_MAX_FRAMES


class TestDedupFaces:
    def test_collapses_same_person_keeps_best_crop(self):
        a_low = _face([1, 0, 0], det_score=0.5)
        a_high = _face([1, 0, 0], det_score=0.95)
        b = _face([0, 1, 0], det_score=0.8)
        out = iv._dedup_faces([(a_low, Path("/f0.jpg")), (a_high, Path("/f1.jpg")), (b, Path("/f2.jpg"))])
        assert len(out) == 2  # two distinct people
        a_cluster = [f for f, _ in out if f.embedding[0] == 1][0]
        assert a_cluster.det_score == 0.95  # kept the higher-confidence crop

    def test_distinct_people_not_merged(self):
        out = iv._dedup_faces([(_face([1, 0, 0]), Path("/a")), (_face([0, 1, 0]), Path("/b"))])
        assert len(out) == 2


class TestDetectVideoFaces:
    def test_returns_empty_without_ffmpeg(self, tmp_path):
        with patch("yaffo.utils.index_video.get_ffmpeg_path", return_value=None):
            assert iv.detect_video_faces(Path("/clip.mov"), tmp_path, 10.0) == []

    def test_samples_dedups_and_returns_face_data(self, tmp_path):
        face = _face([1, 0, 0], det_score=0.9)

        def fake_grab(ffmpeg, video, offset, out):
            Path(out).write_bytes(b"jpeg")
            return True

        with patch("yaffo.utils.index_video.get_ffmpeg_path", return_value=Path("/bin/ffmpeg")), \
             patch("yaffo.utils.index_video._grab_frame", side_effect=fake_grab), \
             patch("yaffo.utils.index_video.image_from_path", return_value=object()), \
             patch("yaffo.utils.index_video.image_to_numpy", return_value=np.zeros((4, 4, 3))), \
             patch("yaffo.utils.index_video.detect_faces", return_value=[face]), \
             patch("yaffo.utils.index_video.save_face_thumbnail",
                   side_effect=lambda f, i, d, loc: tmp_path / f"thumb_{i}.jpg"):
            # duration 6 -> 2 sampled frames; same person each -> dedups to one face.
            faces = iv.detect_video_faces(Path("/clip.mov"), tmp_path, 6.0)

        assert len(faces) == 1
        f = faces[0]
        assert f["det_score"] == 0.9
        assert f["full_file_path"].endswith("thumb_0.jpg")
        assert f["location_right"] == 10 and f["estimated_age"] == 30.0
