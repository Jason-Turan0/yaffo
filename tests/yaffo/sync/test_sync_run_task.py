"""The sync run as the run history sees it (a Job with outcome codes), and the delete
paths that queue removals and announce them."""
import json

import pytest

from yaffo.background_tasks.tasks import sync_run
from yaffo.db.models import (
    Job, SyncItem, EVENT_MEDIA_DELETED, JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, SYNC_ITEM_REMOVING,
)
from yaffo.db.repositories import media_repository
from yaffo.sync import deletes, setup
from yaffo.sync.schemas import Selection
from yaffo.utils import job_codes as codes
from yaffo.utils.index_photos import delete_orphaned_media_items

pytestmark = pytest.mark.unit


@pytest.fixture
def target(session, add_photo, album, tmp_path):
    folder = tmp_path / "out"
    folder.mkdir()
    automation = setup.create_sync(session, "For Mom", adapter_id=2)
    setup.set_options(session, automation, {"folder": str(folder)})
    setup.set_selection(session, automation,
                        Selection(kind="album", album_id=album("For Mom", [add_photo("a.jpg")]).id))
    setup.turn_on(session, automation)
    return automation


def test_a_run_is_recorded_as_a_completed_job_with_its_counts(session, target):
    job = sync_run.run_and_record_sync(session, target, "run-1")

    assert job.status == JOB_STATUS_COMPLETED and job.automation_id == target.id
    data = json.loads(job.job_data)
    assert data["outcome"] == codes.OUTCOME_SYNC_SENT and data["sent"] == 1
    assert "problem" not in data
    assert job.completed_at is not None


def test_a_setup_change_shows_as_needing_consent(session, target, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    setup.set_options(session, target, {"folder": str(other)})

    job = sync_run.run_and_record_sync(session, target, "run-1")

    data = json.loads(job.job_data)
    assert (data["outcome"], data["problem"]) == (codes.OUTCOME_SYNC_NOT_RUN, codes.PROBLEM_SYNC_NEEDS_CONSENT)


def test_an_engine_crash_fails_the_job_instead_of_the_worker(session, target, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(sync_run, "run_sync", boom)

    job = sync_run.run_and_record_sync(session, target, "run-1")

    assert job.status == JOB_STATUS_FAILED and job.error == "disk on fire"
    assert json.loads(job.job_data)["problem"] == codes.PROBLEM_TASK_ERROR


def test_a_finished_run_is_not_repeated_on_a_queue_retry(session, target):
    sync_run.run_and_record_sync(session, target, "run-1")
    assert sync_run.run_and_record_sync(session, target, "run-1") is None
    assert session.query(Job).count() == 1


@pytest.mark.parametrize("delete", ["ui_or_automation", "file_vanished"])
def test_every_delete_path_queues_removals_and_announces_them(session, target, monkeypatch, delete):
    announced: list[tuple[str, dict]] = []
    monkeypatch.setattr(deletes, "emit_event", lambda event, payload: announced.append((event, payload)))
    sync_run.run_and_record_sync(session, target, "run-1")
    photo_id = session.query(SyncItem).one().media_item_id

    if delete == "ui_or_automation":
        media_repository.delete_media_items(session, [photo_id])
    else:
        delete_orphaned_media_items(session, [photo_id])

    row = session.query(SyncItem).one()
    assert (row.status, row.media_item_id) == (SYNC_ITEM_REMOVING, None)
    assert announced == [(EVENT_MEDIA_DELETED, {"media_item_ids": [photo_id], "sync_automation_ids": [target.id]})]
