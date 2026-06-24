from pathlib import Path

import pytest
from PIL import Image

from yaffo.background_tasks.tasks import find_duplicates as mod
from yaffo.utils.ffmpeg_path import is_ffmpeg_available

pytestmark = pytest.mark.unit
MP4_TEST_DATA = Path(__file__).parents[1] / "utils" / "test_data" / "mp4"


def _write_image(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (32, 32), color=color).save(path)


def test_video_hash_uses_indexed_poster_and_is_separate_from_photo_hash(monkeypatch, tmp_path):
    poster = tmp_path / "poster.jpg"
    photo = tmp_path / "photo.jpg"
    video = tmp_path / "video.mp4"
    _write_image(poster, (20, 40, 60))
    _write_image(photo, (20, 40, 60))
    video.touch()

    monkeypatch.setattr(
        mod,
        "extract_poster",
        lambda *args, **kwargs: pytest.fail("indexed video should use its existing poster"),
    )

    video_hash = mod._media_hash(str(video), {str(video): str(poster)}, tmp_path)
    photo_hash = mod._media_hash(str(photo), {}, tmp_path)

    assert video_hash.startswith("video:")
    assert photo_hash.startswith("photo:")
    assert video_hash.removeprefix("video:") == photo_hash.removeprefix("photo:")
    assert video_hash != photo_hash


def test_video_hash_extracts_poster_when_video_is_not_indexed(monkeypatch, tmp_path):
    video = tmp_path / "video.mov"
    extracted_poster = tmp_path / "extracted.jpg"
    video.touch()
    _write_image(extracted_poster, (100, 120, 140))
    calls = []

    def fake_extract_poster(video_path, thumbnail_dir, duration_seconds):
        calls.append((video_path, thumbnail_dir, duration_seconds))
        return extracted_poster

    monkeypatch.setattr(mod, "extract_poster", fake_extract_poster)

    media_hash = mod._media_hash(str(video), {}, tmp_path)

    assert media_hash.startswith("video:")
    assert calls == [(video, tmp_path, None)]


def test_duplicate_videos_produce_the_same_hash(monkeypatch, tmp_path):
    poster_a = tmp_path / "poster-a.jpg"
    poster_b = tmp_path / "poster-b.jpg"
    video_a = tmp_path / "a.mp4"
    video_b = tmp_path / "b.mp4"
    _write_image(poster_a, (80, 100, 120))
    _write_image(poster_b, (80, 100, 120))
    video_a.touch()
    video_b.touch()

    posters = {
        str(video_a): str(poster_a),
        str(video_b): str(poster_b),
    }

    assert mod._media_hash(str(video_a), posters, tmp_path) == mod._media_hash(
        str(video_b), posters, tmp_path
    )


@pytest.mark.skipif(not is_ffmpeg_available(), reason="ffmpeg is required for real video hashing")
def test_duplicate_mp4_fixtures_produce_the_same_hash(tmp_path):
    video = MP4_TEST_DATA / "1mb-example-video-file.mp4"
    duplicate = MP4_TEST_DATA / "1mb-example-video-file_dup.mp4"

    video_hash = mod._media_hash(str(video), {}, tmp_path)
    duplicate_hash = mod._media_hash(str(duplicate), {}, tmp_path)

    assert video_hash.startswith("video:")
    assert video_hash == duplicate_hash
