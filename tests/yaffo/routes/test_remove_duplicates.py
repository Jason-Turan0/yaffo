from pathlib import Path

import pytest

from yaffo.routes.utilities import remove_duplicates as mod

pytestmark = pytest.mark.unit


def test_collect_media_paths_includes_photos_and_videos(monkeypatch, tmp_path):
    photo = tmp_path / "photo.jpg"
    video = tmp_path / "video.mp4"
    ignored = tmp_path / "notes.txt"
    photo.touch()
    video.touch()
    ignored.touch()
    monkeypatch.setattr(mod, "get_thumbnail_dir", lambda: None)
    monkeypatch.setattr(mod, "is_system_file", lambda name: False)

    paths = set(mod.collect_media_paths([str(tmp_path)]))

    assert paths == {str(photo), str(video)}


def test_collect_media_paths_supports_all_cataloged_video_extensions(monkeypatch, tmp_path):
    videos = [tmp_path / f"clip{extension}" for extension in (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".wmv", ".flv")]
    for video in videos:
        video.touch()
    monkeypatch.setattr(mod, "get_thumbnail_dir", lambda: None)
    monkeypatch.setattr(mod, "is_system_file", lambda name: False)

    paths = set(mod.collect_media_paths([str(tmp_path)]))

    assert paths == {str(video) for video in videos}
