"""Serving .heic media items as browser-viewable JPEG.

The conversion must survive files whose extension lies about their format —
phone exports sometimes produce a JPEG named .heic, and read_heif rejects
those ('No ftyp box'), so the route has to fall back to a plain image open."""
import io
from pathlib import Path

from PIL import Image

from yaffo.db import db
from yaffo.db.models import MediaItem

REAL_HEIC = Path(__file__).parents[1] / "utils" / "test_data" / "heic" / "IMG_5195.HEIC"


def _add_media_item(path: Path) -> int:
    media_item = MediaItem(full_file_path=str(path))
    db.session.add(media_item)
    db.session.commit()
    return media_item.id


def test_real_heic_served_as_jpeg(app, client):
    media_item_id = _add_media_item(REAL_HEIC)

    response = client.get(f"/media/{media_item_id}")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/jpeg"


def test_jpeg_mislabeled_as_heic_served_as_jpeg(app, client, tmp_path):
    disguised = tmp_path / "actually_a_jpeg.heic"
    Image.new("RGB", (32, 24), "red").save(disguised, format="JPEG")
    media_item_id = _add_media_item(disguised)

    response = client.get(f"/media/{media_item_id}")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/jpeg"
    assert Image.open(io.BytesIO(response.data)).size == (32, 24)