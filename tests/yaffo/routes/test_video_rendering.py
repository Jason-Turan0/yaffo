"""Video rows render with their video affordances: the gallery shows a play badge
and a formatted duration over a placeholder poster; the detail view swaps the
<img> for a <video> player and prints the video-metadata block."""
import pytest

from yaffo.db import db
from yaffo.db.models import MediaItem, MEDIA_TYPE_VIDEO


@pytest.fixture
def video_id(app):
    with app.app_context():
        video = MediaItem(
            full_file_path="/media/organized/2023/clip.mov",
            media_type=MEDIA_TYPE_VIDEO,
            duration_seconds=187,
            width=1920,
            height=1080,
            video_codec="hevc",
        )
        db.session.add(video)
        db.session.commit()
        return video.id


def test_gallery_shows_play_badge_and_duration(client, video_id):
    body = client.get("/").data.decode()
    assert "video-play-badge" in body
    assert "video-duration" in body
    assert "3:07" in body  # 187s formatted
    assert "video_placeholder.svg" in body


def test_play_badge_is_a_button_wired_for_inline_play(client, video_id):
    # The badge is a real button carrying the media id, and the inline-play module
    # is loaded — clicking it previews in place instead of opening the view screen.
    body = client.get("/").data.decode()
    assert f'<button type="button" class="video-play-badge"' in body
    assert f'data-photo-id="{video_id}"' in body
    assert "media/gallery_video.js" in body


def test_detail_view_renders_video_player_and_metadata(client, video_id):
    body = client.get(f"/media/view/{video_id}").data.decode()
    assert "<video" in body
    assert f"/media/{video_id}" in body  # range-served source
    assert "1920 × 1080" in body
    assert "hevc" in body
    assert "3:07" in body
