"""Scheduled automations receive only indexed media in their selected roots."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.schedule_scope import media_item_ids, selected_paths
from yaffo.db import db
from yaffo.db.models import Automation, AutomationTrigger, Job, MediaItem, JOB_STATUS_FAILED, TRIGGER_TYPE_SCHEDULE
from yaffo.db.repositories import media_dir_repository

pytestmark = pytest.mark.unit


def test_schedule_resolves_media_dirs_and_folders(tmp_path, monkeypatch):
    from yaffo.background_tasks.tasks import dispatcher

    engine = create_engine(f"sqlite:///{tmp_path / 'schedule.db'}")
    db.metadata.create_all(engine)
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    for root in (root_a, root_b):
        root.mkdir()
    folder = root_a / "trip_%"
    folder.mkdir()
    entries = [media_dir_repository.MediaDir("a", root_a), media_dir_repository.MediaDir("b", root_b)]
    monkeypatch.setattr(media_dir_repository, "get_media_dir_entries", lambda session: entries)
    with Session(engine) as session:
        for path in (folder / "one.jpg", root_a / "other.jpg", root_b / "two.jpg",
                     root_a / "trip_other" / "wrong.jpg"):
            session.add(MediaItem(full_file_path=str(path)))
        automation = Automation(slug="test", name="Test", enabled=True, published_code="print('ok')")
        session.add(automation)
        session.flush()
        trigger = AutomationTrigger(
            automation_id=automation.id, trigger_type=TRIGGER_TYPE_SCHEDULE,
            enabled=True, cron="* * * * *", next_run_at=datetime(2026, 1, 1),
            config={"media_dir_ids": ["b"], "folder_paths": [str(folder)]},
        )
        session.add(trigger)
        session.commit()

        all_paths = selected_paths(session, None)
        assert len(media_item_ids(session, all_paths)) == 4
        scoped = selected_paths(session, trigger.config)
        assert set(media_item_ids(session, scoped)) == {1, 3}
        with pytest.raises(ValueError):
            selected_paths(session, {"media_dir_ids": ["removed"]})
        with pytest.raises(ValueError):
            selected_paths(session, {"scope_type": "paths", "folder_paths": [str(tmp_path / "outside")]})

    seen = []
    def session_factory():
        return Session(engine)
    session_factory.remove = lambda: None
    monkeypatch.setattr(dispatcher, "SessionFactory", session_factory)
    monkeypatch.setattr(dispatcher, "invoke_automation", lambda automation, context: seen.append(context) or True)
    monkeypatch.setattr(dispatcher, "utcnow", lambda: datetime(2026, 1, 1, 0, 1))
    dispatcher.dispatch_scheduled_tasks.fn()
    assert len(seen) == 1
    assert seen[0].event_type is None
    assert seen[0].media_item_ids == [1, 3]
    assert set(seen[0].scope_paths) == {str(root_b), str(folder)}

    with Session(engine) as session:
        trigger = session.query(AutomationTrigger).one()
        trigger.config = {"scope_type": "paths", "folder_paths": [str(tmp_path / "outside")]}
        trigger.next_run_at = datetime(2026, 1, 1, 0, 1)
        session.commit()
    monkeypatch.setattr(dispatcher, "utcnow", lambda: datetime(2026, 1, 1, 0, 2))
    dispatcher.dispatch_scheduled_tasks.fn()
    with Session(engine) as session:
        failed = session.query(Job).one()
        trigger = session.query(AutomationTrigger).one()
        assert failed.automation_id == trigger.automation_id
        assert failed.status == JOB_STATUS_FAILED
        assert failed.error_count == 1
        assert trigger.last_run_at == datetime(2026, 1, 1, 0, 1)
        assert trigger.next_run_at > datetime(2026, 1, 1, 0, 2)
    dispatcher.dispatch_scheduled_tasks.fn()
    with Session(engine) as session:
        assert session.query(Job).count() == 1
    engine.dispose()
