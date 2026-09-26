"""Unit tests for the filter-layout registry + persistence (filter_config).

The saved layout (a JSON list of {key, visible}) is merged onto the registry on
read: known keys keep their saved order/visibility, unknown keys drop, and any
registry filter missing from the save is appended visible.
"""
import re
from pathlib import Path
from urllib.parse import parse_qsl

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from werkzeug.datastructures import MultiDict

import yaffo
from yaffo.db import db
from yaffo.db.models import ApplicationSettings
from yaffo.domain.media_filter_params import MEDIA_FILTER_PARAMS
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


def test_every_filter_parameter_belongs_to_exactly_one_control():
    """The configurator's controls cover the filter table: nothing unowned (it
    would have no control) and nothing owned twice."""
    owners = {}
    for control in fc.FILTERS:
        for key in control.params:
            owners.setdefault(key, []).append(control.key)
    assert {p.key for p in MEDIA_FILTER_PARAMS} == set(owners)
    assert {key: keys for key, keys in owners.items() if len(keys) > 1} == {}


def test_each_control_template_renders_the_parameters_it_owns():
    param_by_key = {p.key: p.param for p in MEDIA_FILTER_PARAMS}
    templates = Path(yaffo.__file__).parent / "templates"
    for control in fc.FILTERS:
        source = (templates / control.template).read_text()
        names = set(re.findall(r'name="([a-z-]+)"', source)) | set(re.findall(r'render_match_type\("([a-z-]+)"', source))
        assert {param_by_key[key] for key in control.params} <= names, control.key


@pytest.mark.parametrize("query, expected", [
    ("", 0),
    ("person=1&person=2&person-match-type=all", 1),  # one control, however many values
    ("person-match-type=all", 0),  # a match type alone narrows nothing
    ("tag-name=event&tag-value=x&year=2020", 2),
    ("proximity-lat=1&proximity-lon=2&proximity-distance=5&proximity-location=X&unnamed=1", 1),
    ("page=2&view=grid&shape=round", 0),  # page state, and an invalid value
])
def test_badge_counts_filter_controls(query, expected):
    assert fc.applied_count(MultiDict(parse_qsl(query))) == expected
