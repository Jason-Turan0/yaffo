"""Video rows render with their video affordances: the gallery shows a play badge
and a formatted duration over a placeholder poster; the detail view swaps the
<img> for a <video> player and prints the video-metadata block."""
import pytest

from yaffo.db import db
from yaffo.db.models import MediaItem, MEDIA_TYPE_VIDEO


@pytest.fixture
def video_id(app, tmp_path):
    # A real file on disk so the detail view renders the player (it checks existence).
    clip = tmp_path / "clip.mov"
    clip.write_bytes(b"\x00\x00\x00\x18ftypqt  ")
    with app.app_context():
        video = MediaItem(
            full_file_path=str(clip),
            media_type=MEDIA_TYPE_VIDEO,
            duration_seconds=187,
            width=1920,
            height=1080,
            video_codec="hevc",
        )
        db.session.add(video)
        db.session.commit()
        return video.id


@pytest.fixture
def missing_video_id(app):
    with app.app_context():
        video = MediaItem(
            full_file_path="/media/organized/2023/gone.mov",  # not on disk
            media_type=MEDIA_TYPE_VIDEO,
            duration_seconds=187,
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


def test_detail_view_shows_unavailable_when_file_missing(client, missing_video_id):
    body = client.get(f"/media/view/{missing_video_id}").data.decode()
    assert "media-missing" in body
    assert "no longer available" in body
    assert "<video" not in body  # no broken player


def test_media_route_404s_when_file_missing(client, missing_video_id):
    assert client.get(f"/media/{missing_video_id}").status_code == 404


def test_locations_payload_includes_media_type_for_videos(client, app):
    # A geotagged video must carry media_type so the map popup uses its poster
    # (not /media/<id>, which would render the raw clip as a broken <img>).
    with app.app_context():
        v = MediaItem(
            full_file_path="/media/2023/clip.mov", media_type=MEDIA_TYPE_VIDEO,
            latitude=38.77, longitude=-90.48, location_name="St. Louis",
        )
        db.session.add(v)
        db.session.commit()
    body = client.get("/locations").data.decode()
    assert '"media_type": "video"' in body


@pytest.fixture
def unplayable_video_id(app, tmp_path):
    clip = tmp_path / "clip.mkv"  # cataloged but not browser-playable
    clip.write_bytes(b"\x1aE\xdf\xa3")  # EBML magic; just needs to exist
    with app.app_context():
        v = MediaItem(full_file_path=str(clip), media_type=MEDIA_TYPE_VIDEO, duration_seconds=12)
        db.session.add(v)
        db.session.commit()
        return v.id


def test_detail_view_offers_open_externally_for_unplayable_format(client, unplayable_video_id):
    body = client.get(f"/media/view/{unplayable_video_id}").data.decode()
    assert "can't play in the browser" in body
    assert "Open in default player" in body
    assert "<video" not in body  # no inline player attempted


def test_gallery_unplayable_video_has_no_play_badge(client, unplayable_video_id):
    body = client.get("/").data.decode()
    # No play overlay at all for a non-playable video (the only media item here);
    # its poster still renders and the card click opens the detail view.
    assert "video-play-badge" not in body
    assert "video_placeholder.svg" in body or "media_poster" in body
