"""Finding face crops and posters whose files are gone, and writing them again at
their recorded paths without touching the faces (so people and ignored status
survive), through to the one background task that runs a repair."""
import json
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.tasks import complete_job
from yaffo.background_tasks.tasks.regenerate_thumbnails import regenerate_thumbnails_task
from yaffo.background_tasks.utils import SessionFactory, engine as prod_engine
from yaffo.db import db
from yaffo.db.models import (
    FACE_STATUS_ASSIGNED,
    FACE_STATUS_IGNORED,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    MEDIA_TYPE_VIDEO,
    Face,
    Job,
    MediaItem,
    Person,
    PersonFace,
)
from yaffo.utils import thumbnail_repair
from yaffo.utils.index_video import face_crop_frame_index
from yaffo.utils.thumbnail_marker import THUMBNAIL_DIR_MARKER
from yaffo.utils.thumbnail_repair import (
    THUMBNAIL_REPAIR_JOB,
    MissingThumbnails,
    find_missing_thumbnails,
    repair_item_thumbnails,
    start_thumbnail_repair,
    thumbnail_dir_available,
)

pytestmark = pytest.mark.unit

FACE_BOX = dict(location_top=10, location_right=70, location_bottom=90, location_left=20)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lib.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def thumbs(tmp_path) -> Path:
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    (thumbs / THUMBNAIL_DIR_MARKER).write_text("marker")
    return thumbs


def _photo(tmp_path: Path, name: str = "photo.jpg") -> Path:
    path = tmp_path / name
    Image.new("RGB", (400, 300), "orange").save(path, "JPEG")
    return path


def _add_item(session, path: Path, **fields) -> MediaItem:
    item = MediaItem(full_file_path=str(path), **fields)
    session.add(item)
    session.flush()
    return item


def _add_face(session, item: MediaItem, crop: Path, **fields) -> Face:
    face = Face(media_item_id=item.id, full_file_path=str(crop), **FACE_BOX, **fields)
    session.add(face)
    session.flush()
    return face


# ---- finding what's missing -----------------------------------------------------------

def test_counts_missing_crops_and_posters_per_rebuildable_item(session, tmp_path, thumbs):
    photo = _add_item(session, _photo(tmp_path))
    _add_face(session, photo, thumbs / "face_photo_0_aaaa.jpg")
    _add_face(session, photo, thumbs / "face_photo_1_bbbb.jpg")
    kept = thumbs / "face_photo_2_cccc.jpg"
    kept.write_bytes(b"jpg")
    _add_face(session, photo, kept)
    video_file = tmp_path / "clip.mov"
    video_file.write_bytes(b"mov")
    _add_item(session, video_file, media_type=MEDIA_TYPE_VIDEO, poster_path=str(thumbs / "poster_1.jpg"))
    # Its own file is gone: nothing to rebuild from, and the scan lists it as orphaned.
    gone = _add_item(session, tmp_path / "deleted.jpg")
    _add_face(session, gone, thumbs / "face_deleted_0_dddd.jpg")
    session.commit()

    missing = find_missing_thumbnails(session, thumbs)

    assert missing.available
    assert (missing.faces, missing.posters, missing.total) == (2, 1, 3)
    assert missing.media_item_ids == [1, 2]


def test_a_folder_that_isnt_there_reads_unavailable_not_all_missing(session, tmp_path):
    item = _add_item(session, _photo(tmp_path))
    _add_face(session, item, tmp_path / "unplugged" / "face_photo_0_aaaa.jpg")
    session.commit()

    assert not find_missing_thumbnails(session, None).available
    assert not find_missing_thumbnails(session, tmp_path / "unplugged").available
    empty_mount_point = tmp_path / "empty"
    empty_mount_point.mkdir()
    assert not thumbnail_dir_available(empty_mount_point)


def test_a_folder_whose_thumbnails_were_all_deleted_still_counts(session, tmp_path, thumbs):
    # Only the hidden marker is left, which is what an emptied (not unmounted) folder looks like.
    item = _add_item(session, _photo(tmp_path))
    _add_face(session, item, thumbs / "face_photo_0_aaaa.jpg")
    session.commit()

    missing = find_missing_thumbnails(session, thumbs)

    assert missing.available and missing.faces == 1


# ---- rebuilding -----------------------------------------------------------------------

def test_rebuilds_a_photo_face_crop_at_its_recorded_path(session, tmp_path, thumbs):
    item = _add_item(session, _photo(tmp_path))
    crop = thumbs / "face_photo_0_aaaa.jpg"
    _add_face(session, item, crop)
    existing = thumbs / "face_photo_1_bbbb.jpg"
    existing.write_bytes(b"left alone")
    _add_face(session, item, existing)

    repair = repair_item_thumbnails(session, item)

    assert (repair.written, repair.failures) == (1, [])
    with Image.open(crop) as image:
        assert image.size == (50, 80)  # the stored box, under the 150px thumbnail bound
    assert existing.read_bytes() == b"left alone"


def test_rebuilding_leaves_people_and_ignored_status_alone(session, tmp_path, thumbs):
    item = _add_item(session, _photo(tmp_path))
    assigned = _add_face(session, item, thumbs / "face_photo_0_aaaa.jpg", status=FACE_STATUS_ASSIGNED)
    ignored = _add_face(session, item, thumbs / "face_photo_1_bbbb.jpg", status=FACE_STATUS_IGNORED)
    person = Person(name="Ada")
    session.add(person)
    session.flush()
    session.add(PersonFace(person_id=person.id, face_id=assigned.id, similarity=0.9))
    session.commit()

    repair_item_thumbnails(session, item)
    session.commit()

    assert session.query(Face).count() == 2
    assert session.get(Face, assigned.id).status == FACE_STATUS_ASSIGNED
    assert session.get(Face, ignored.id).status == FACE_STATUS_IGNORED
    assert session.query(PersonFace).one().face_id == assigned.id


def test_rebuilds_video_faces_from_the_frame_their_name_records(session, tmp_path, thumbs, monkeypatch):
    video_file = tmp_path / "clip.mov"
    video_file.write_bytes(b"mov")
    item = _add_item(session, video_file, media_type=MEDIA_TYPE_VIDEO, duration_seconds=30.0)
    first = thumbs / "face_clip_f2_0_aaaa.jpg"
    second = thumbs / "face_clip_f2_1_bbbb.jpg"
    _add_face(session, item, first)
    _add_face(session, item, second)
    grabbed = []

    def fake_frame(video, index, duration, out_path):
        grabbed.append((video, index, duration))
        Image.new("RGB", (320, 180), "blue").save(out_path, "PNG")
        return True

    monkeypatch.setattr(thumbnail_repair, "write_sampled_frame", fake_frame)

    repair = repair_item_thumbnails(session, item)

    assert (repair.written, repair.failures) == (2, [])
    assert grabbed == [(video_file, 2, 30.0)]  # both faces came from frame 2: grabbed once
    assert first.exists() and second.exists()


def test_a_video_face_that_cant_be_placed_is_reported_not_guessed(session, tmp_path, thumbs, monkeypatch):
    video_file = tmp_path / "clip.mov"
    video_file.write_bytes(b"mov")
    item = _add_item(session, video_file, media_type=MEDIA_TYPE_VIDEO, duration_seconds=30.0)
    _add_face(session, item, thumbs / "face_renamed_0_aaaa.jpg")
    _add_face(session, item, thumbs / "face_clip_f1_0_bbbb.jpg")
    monkeypatch.setattr(thumbnail_repair, "write_sampled_frame", lambda *a: False)

    repair = repair_item_thumbnails(session, item)

    assert repair.written == 0
    assert len(repair.failures) == 2
    assert "not named after a sampled frame" in repair.failures[0]
    assert "could not read frame 1" in repair.failures[1]


def test_rebuilds_a_missing_poster(session, tmp_path, thumbs, monkeypatch):
    video_file = tmp_path / "clip.mov"
    video_file.write_bytes(b"mov")
    poster = thumbs / "poster_0123456789abcdef.jpg"
    item = _add_item(session, video_file, media_type=MEDIA_TYPE_VIDEO, duration_seconds=8.0,
                     poster_path=str(poster))
    calls = []
    monkeypatch.setattr(thumbnail_repair, "write_poster",
                        lambda video, path, duration: calls.append((video, path, duration)) or True)

    repair = repair_item_thumbnails(session, item)

    assert repair.written == 1
    assert calls == [(video_file, poster, 8.0)]


@pytest.mark.parametrize("name, index", [
    ("face_clip_f12_0_aaaa.jpg", 12),
    ("face_clip_f0_3_aaaa.jpg", 0),
    ("face_clip_0_aaaa.jpg", None),       # a photo-style name
    ("face_other_f2_0_aaaa.jpg", None),   # another video's crop
])
def test_face_crop_frame_index(name, index):
    assert face_crop_frame_index(Path(name), Path("/media/clip.mov")) == index


# ---- the background job ---------------------------------------------------------------

@pytest.fixture
def immediate_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}", connect_args={'check_same_thread': False})
    db.metadata.create_all(engine)
    SessionFactory.configure(bind=engine)
    task_queue.immediate = True
    monkeypatch.setattr(complete_job, "emit_job_completed_event", lambda session, job: None)
    try:
        yield engine
    finally:
        task_queue.immediate = False
        SessionFactory.remove()
        SessionFactory.configure(bind=prod_engine)
        engine.dispose()


def test_the_job_rebuilds_every_item_and_completes(immediate_db, tmp_path, thumbs):
    with Session(immediate_db) as session:
        items = [_add_item(session, _photo(tmp_path, f"p{i}.jpg")) for i in range(3)]
        for i, item in enumerate(items):
            _add_face(session, item, thumbs / f"face_p{i}_0_aaaa.jpg")
        _add_face(session, items[0], thumbs / "face_p0_1_bbbb.jpg")  # two crops on one photo
        session.commit()
        ids = [item.id for item in items]
        job_id = start_thumbnail_repair(session, find_missing_thumbnails(session, thumbs))

    with Session(immediate_db) as session:
        job = session.get(Job, job_id)
        assert job.name == THUMBNAIL_REPAIR_JOB
        assert job.status == JOB_STATUS_COMPLETED
        # Counted in thumbnail files, not photos.
        assert (job.task_count, job.completed_count, job.error_count) == (4, 4, 0)
        assert job.started_at is not None and job.completed_at is not None
        assert json.loads(job.job_data) == {"media_item_ids": ids, "outcome": "regenerated",
                                            "written": 4, "total": 4}
    assert all((thumbs / f"face_p{i}_0_aaaa.jpg").exists() for i in range(3))


def test_the_job_recounts_what_is_missing_when_it_runs(immediate_db, tmp_path, thumbs, monkeypatch):
    with Session(immediate_db) as session:
        item = _add_item(session, _photo(tmp_path))
        _add_face(session, item, thumbs / "face_photo_0_aaaa.jpg")
        back = thumbs / "face_photo_1_bbbb.jpg"
        _add_face(session, item, back)
        session.commit()
        missing = find_missing_thumbnails(session, thumbs)
        assert missing.total == 2
        back.write_bytes(b"came back before the job ran")
        job_id = start_thumbnail_repair(session, missing)

    with Session(immediate_db) as session:
        job = session.get(Job, job_id)
        assert (job.task_count, job.completed_count, job.error_count) == (1, 1, 0)
        assert json.loads(job.job_data)["written"] == 1


def test_an_item_that_cant_be_rebuilt_counts_as_an_error_with_its_reason(immediate_db, tmp_path, thumbs):
    with Session(immediate_db) as session:
        good = _add_item(session, _photo(tmp_path))
        _add_face(session, good, thumbs / "face_photo_0_aaaa.jpg")
        broken_file = tmp_path / "broken.jpg"
        broken_file.write_bytes(b"not an image")
        broken = _add_item(session, broken_file)
        _add_face(session, broken, thumbs / "face_broken_0_bbbb.jpg")
        session.commit()
        missing = find_missing_thumbnails(session, thumbs)
        # An item deleted after the count has no faces left to rebuild: not an error.
        missing = MissingThumbnails(True, faces=missing.faces, media_item_ids=missing.media_item_ids + [999])
        job_id = start_thumbnail_repair(session, missing)

    with Session(immediate_db) as session:
        job = session.get(Job, job_id)
        assert job.status == JOB_STATUS_COMPLETED
        assert (job.task_count, job.completed_count, job.error_count) == (2, 1, 1)
        assert "face_broken_0_bbbb.jpg" in job.error
        assert json.loads(job.job_data)["written"] == 1


def test_a_job_cancelled_before_it_runs_does_no_work(immediate_db, tmp_path, thumbs, monkeypatch):
    with Session(immediate_db) as session:
        item = _add_item(session, _photo(tmp_path))
        crop = thumbs / "face_photo_0_aaaa.jpg"
        _add_face(session, item, crop)
        session.commit()
        # Cancel between the Job row and the task: the task is queued, not run yet.
        monkeypatch.setattr(task_queue, "immediate", False)
        monkeypatch.setattr("yaffo.background_tasks.tasks.regenerate_thumbnails."
                            "regenerate_thumbnails_task", lambda job_id: None)
        job_id = start_thumbnail_repair(session, find_missing_thumbnails(session, thumbs))
        session.get(Job, job_id).status = JOB_STATUS_CANCELLED
        session.commit()

    regenerate_thumbnails_task.fn(job_id)

    with Session(immediate_db) as session:
        job = session.get(Job, job_id)
        assert job.status == JOB_STATUS_CANCELLED
        assert job.completed_at is not None  # stopped, so it reads Cancelled, not Stopping
    assert not crop.exists()


def test_a_cancel_mid_run_stops_at_the_next_check_and_counts_the_rest(immediate_db, tmp_path, thumbs, monkeypatch):
    from yaffo.background_tasks.tasks import regenerate_thumbnails as task_module
    count = task_module.PROGRESS_EVERY + 5
    with Session(immediate_db) as session:
        items = [_add_item(session, _photo(tmp_path, f"p{i}.jpg")) for i in range(count)]
        for i, item in enumerate(items):
            _add_face(session, item, thumbs / f"face_p{i}_0_aaaa.jpg")
        session.commit()
        ids = [item.id for item in items]

        def cancel_after_first_tick(session_, job_id):
            session_.query(Job).filter_by(id=job_id).update({"status": JOB_STATUS_CANCELLED})
            session_.commit()
            return True

        monkeypatch.setattr(task_module, "is_job_cancelled", cancel_after_first_tick)
        job_id = start_thumbnail_repair(session, find_missing_thumbnails(session, thumbs))

    with Session(immediate_db) as session:
        job = session.get(Job, job_id)
        assert job.status == JOB_STATUS_CANCELLED
        assert (job.completed_count, job.cancelled_count) == (task_module.PROGRESS_EVERY, 5)
        assert job.completed_at is not None
        assert "outcome" not in json.loads(job.job_data)  # a cancelled run has no result
