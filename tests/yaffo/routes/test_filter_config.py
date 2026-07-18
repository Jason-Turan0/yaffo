"""Unit tests for the filter-layout registry + persistence (filter_config).

The saved layout (a JSON list of {key, visible}) is merged onto the registry on
read: known keys keep their saved order/visibility, unknown keys drop, and any
registry filter missing from the save is appended visible.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import ApplicationSettings
from yaffo.routes import filter_config as fc

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


class TestDefault:
    def test_unsaved_layout_is_all_filters_visible_in_registry_order(self, session):
        layout = fc.load_layout(session)
        assert [i.key for i in layout] == fc.default_keys()
        assert all(i.visible for i in layout)


class TestSaveLoad:
    def test_roundtrips_order_and_visibility(self, session):
        fc.save_layout(session, [{"key": "device", "visible": True}, {"key": "year", "visible": False}])
        layout = fc.load_layout(session)
        # saved keys come first in saved order...
        assert (layout[0].key, layout[0].visible) == ("device", True)
        assert (layout[1].key, layout[1].visible) == ("year", False)
        # ...then the remaining registry filters, appended visible
        rest = [i.key for i in layout[2:]]
        assert "device" not in rest and "year" not in rest
        assert all(i.visible for i in layout[2:])
        assert {"device", "year", *rest} == set(fc.default_keys())

    def test_unknown_keys_are_dropped(self, session):
        fc.save_layout(session, [{"key": "bogus", "visible": True}, {"key": "device", "visible": False}])
        layout = fc.load_layout(session)
        assert all(i.key != "bogus" for i in layout)
        assert next(i for i in layout if i.key == "device").visible is False

    def test_duplicate_keys_are_deduped_first_wins(self, session):
        fc.save_layout(session, [{"key": "year", "visible": False}, {"key": "year", "visible": True}])
        years = [i for i in fc.load_layout(session) if i.key == "year"]
        assert len(years) == 1 and years[0].visible is False

    def test_save_overwrites_previous(self, session):
        fc.save_layout(session, [{"key": "year", "visible": False}])
        fc.save_layout(session, [{"key": "month", "visible": False}])
        layout = {i.key: i.visible for i in fc.load_layout(session)}
        assert layout["month"] is False
        assert layout["year"] is True  # no longer in the save -> back to default visible


class TestSharedLayout:
    """One layout for the whole app, stored under a single setting key."""

    def test_layout_is_stored_under_the_shared_key(self, session):
        fc.save_layout(session, [{"key": "year", "visible": False}])

        setting = session.query(ApplicationSettings).filter_by(name=fc.SETTING_NAME).first()
        assert setting is not None
        assert next(i for i in fc.load_layout(session) if i.key == "year").visible is False
