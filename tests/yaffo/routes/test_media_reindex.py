"""Per-photo reindex, from the photo view screen.

Re-runs indexing for one file — the fix when a photo's derived data is wrong (a face
box in the wrong place, a missing size). It re-detects faces, so the photo's person
assignments go with them; the button confirms that before calling this.
"""
from types import SimpleNamespace

from yaffo.db import db
from yaffo.db.models import MediaItem


def test_reindex_enqueues_a_forced_index_job(app, client, monkeypatch, tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"\xff\xd8\xff")
    media_item = MediaItem(full_file_path=str(photo))
    db.session.add(media_item)
    db.session.commit()

    calls = {}
    monkeypatch.setattr(
        "yaffo.routes.media.reindex_media_items",
        lambda session, items: calls.update(items=[item.id for item in items])
        or SimpleNamespace(import_job_id="i1", index_job_id="x1"),
    )

    response = client.post(f"/api/media/{media_item.id}/reindex")

    assert response.status_code == 202
    assert response.get_json() == {"job_id": "x1"}
    assert calls == {"items": [media_item.id]}


def test_reindex_unknown_photo_is_a_404(client):
    response = client.post("/api/media/999999/reindex")

    assert response.status_code == 404
    assert response.get_json()["code"] == "media_item_not_found"


def test_reindex_reports_a_file_that_is_gone_from_disk(app, client, tmp_path):
    """The row is there but the file isn't: indexing it could only fail, so say so
    rather than queueing work that errors out in the background."""
    media_item = MediaItem(full_file_path=str(tmp_path / "vanished.jpg"))
    db.session.add(media_item)
    db.session.commit()

    response = client.post(f"/api/media/{media_item.id}/reindex")

    assert response.status_code == 404
    assert response.get_json()["code"] == "file_not_found"


def test_photo_view_offers_the_reindex_action(app, client, tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"\xff\xd8\xff")
    media_item = MediaItem(full_file_path=str(photo))
    db.session.add(media_item)
    db.session.commit()

    body = client.get(f"/media/view/{media_item.id}").data.decode()

    assert 'id="reindex-btn"' in body
    assert f"photoView.reindex({media_item.id})" in body


def test_photo_view_explains_a_file_that_couldnt_be_indexed(app, client, tmp_path):
    photo = tmp_path / "burst.jpg"
    photo.write_bytes(b"\xff\xd8\xff")
    media_item = MediaItem(full_file_path=str(photo), status="FAILED", index_error="decode_error",
                           index_error_detail="OSError: image file is truncated (78 bytes not processed)")
    db.session.add(media_item)
    db.session.commit()

    body = client.get(f"/media/view/{media_item.id}").data.decode()

    assert "it appears to be damaged or incomplete" in body
    assert "Use Reindex to try again." in body and 'id="reindex-btn"' in body
    assert "<summary>Details</summary>" in body and "image file is truncated (78 bytes not processed)" in body


def test_photo_view_has_no_failure_note_for_an_indexed_photo(app, client, tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"\xff\xd8\xff")
    media_item = MediaItem(full_file_path=str(photo), status="INDEXED")
    db.session.add(media_item)
    db.session.commit()

    assert "index-failure-note" not in client.get(f"/media/view/{media_item.id}").data.decode()
