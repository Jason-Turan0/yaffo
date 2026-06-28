import pytest

from yaffo.db import db
from yaffo.db.models import ApplicationSettings, Face, Job, MediaItem, JOB_STATUS_PENDING

pytestmark = pytest.mark.unit


def _add_pending_import_job():
    db.session.add(Job(
        id="importing",
        name="import_photos",
        status=JOB_STATUS_PENDING,
        task_count=1,
    ))
    db.session.commit()


def test_thumbnail_directory_update_keeps_raw_path(client, app, tmp_path):
    thumbnail_dir = tmp_path / "Faces & Crops"

    response = client.post("/api/settings/thumbnail-dir", json={"directory": str(thumbnail_dir)})

    assert response.status_code == 200
    assert response.get_json()["new_directory"] == str(thumbnail_dir)

    with app.app_context():
        setting = db.session.query(ApplicationSettings).filter_by(name="thumbnail_dir").one()
        assert setting.value == str(thumbnail_dir)
        assert "&amp;" not in setting.value


def test_settings_page_disables_directory_changes_during_library_update(client, app):
    with app.app_context():
        _add_pending_import_job()

    body = client.get("/settings").get_data(as_text=True)

    assert "Media and thumbnail directories cannot be changed while importing or indexing is in progress." in body
    assert 'data-action="add-media-dir"\n                    disabled' in body
    assert 'data-action="change-thumbnail-dir"\n                    disabled' in body


def test_media_directory_add_is_blocked_during_library_update(client, app, tmp_path):
    with app.app_context():
        _add_pending_import_job()

    response = client.post("/api/settings/media-dirs", json={"directory": str(tmp_path / "media")})

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "Media and thumbnail directories cannot be changed while importing or indexing is in progress.",
        "code": "library_update_in_progress",
    }


def test_media_directory_remove_is_blocked_during_library_update(client, app):
    with app.app_context():
        _add_pending_import_job()

    response = client.delete("/api/settings/media-dirs/0")

    assert response.status_code == 409
    assert response.get_json()["code"] == "library_update_in_progress"


def test_thumbnail_directory_update_is_blocked_during_library_update(client, app, tmp_path):
    with app.app_context():
        _add_pending_import_job()

    response = client.post("/api/settings/thumbnail-dir", json={"directory": str(tmp_path / "thumbs")})

    assert response.status_code == 409
    assert response.get_json()["code"] == "library_update_in_progress"


def test_thumbnail_directory_update_rewrites_face_paths(client, app, tmp_path):
    old_dir = tmp_path / "old-thumbs"
    new_dir = tmp_path / "new-thumbs"
    nested = old_dir / "people" / "face_1.jpg"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"thumb")

    with app.app_context():
        media_item = MediaItem(full_file_path=str(tmp_path / "photo.jpg"))
        db.session.add(media_item)
        db.session.flush()
        db.session.add_all([
            ApplicationSettings(name="thumbnail_dir", type="str", value=str(old_dir)),
            Face(media_item_id=media_item.id, full_file_path=str(nested)),
        ])
        db.session.commit()

    response = client.post("/api/settings/thumbnail-dir", json={"directory": str(new_dir)})

    assert response.status_code == 200
    assert response.get_json()["faces_updated"] == 1
    assert not nested.exists()
    assert (new_dir / "people" / "face_1.jpg").exists()
    with app.app_context():
        face = db.session.query(Face).one()
        assert face.full_file_path == str(new_dir / "people" / "face_1.jpg")


def test_thumbnail_directory_update_rewrites_missing_face_paths(client, app, tmp_path):
    old_dir = tmp_path / "old-thumbs"
    new_dir = tmp_path / "new-thumbs"
    missing_face_path = old_dir / "stale" / "face_2.jpg"
    old_dir.mkdir()

    with app.app_context():
        media_item = MediaItem(full_file_path=str(tmp_path / "photo.jpg"))
        db.session.add(media_item)
        db.session.flush()
        db.session.add_all([
            ApplicationSettings(name="thumbnail_dir", type="str", value=str(old_dir)),
            Face(media_item_id=media_item.id, full_file_path=str(missing_face_path)),
        ])
        db.session.commit()

    response = client.post("/api/settings/thumbnail-dir", json={"directory": str(new_dir)})

    assert response.status_code == 200
    assert response.get_json()["faces_updated"] == 1
    with app.app_context():
        face = db.session.query(Face).one()
        assert face.full_file_path == str(new_dir / "stale" / "face_2.jpg")


def test_thumbnail_directory_update_rewrites_video_poster_paths(client, app, tmp_path):
    old_dir = tmp_path / "old-thumbs"
    new_dir = tmp_path / "new-thumbs"
    poster = old_dir / "poster.jpg"
    old_dir.mkdir()
    poster.write_bytes(b"poster")

    with app.app_context():
        db.session.add_all([
            ApplicationSettings(name="thumbnail_dir", type="str", value=str(old_dir)),
            MediaItem(full_file_path=str(tmp_path / "video.mov"), poster_path=str(poster)),
        ])
        db.session.commit()

    response = client.post("/api/settings/thumbnail-dir", json={"directory": str(new_dir)})

    assert response.status_code == 200
    assert response.get_json()["posters_updated"] == 1
    with app.app_context():
        media_item = db.session.query(MediaItem).one()
        assert media_item.poster_path == str(new_dir / "poster.jpg")
