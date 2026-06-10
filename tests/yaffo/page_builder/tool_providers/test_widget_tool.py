"""Unit tests for the widget tools (yaffo/page_builder/tool_providers/widget_tool.py).

The tools persist directly into a working PageVersion, so these exercise them against
a throwaway database: the model-facing schema (derived from WidgetDraft), and the
create/update behaviour — placement (explicit, or bottom-of-grid when omitted),
content merges, and the not-found / missing-id guards.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.repositories import custom_page_repository as repo
from yaffo.page_builder.tool_providers import widget_tool as wt

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


@pytest.fixture
def version_id(session):
    """A version to write into (a fresh page's published version is fine here)."""
    return repo.create_page(session, "Trip").published_version_id


@pytest.fixture
def provider(session, version_id):
    return wt.WidgetToolProvider(version_id, session=session)


def _create_args(**over):
    args = {"title": "A", "data_query": {}, "html": "<div>"}
    args.update(over)
    return args


def _widgets(session, version_id):
    return repo.get_version(session, version_id).widgets


class TestSchema:
    def test_content_fields_include_grid_position(self):
        assert "grid_x" in wt._CONTENT_FIELDS
        assert "grid_y" in wt._CONTENT_FIELDS

    def test_grid_position_is_optional_integer_with_help(self):
        for name in ("grid_x", "grid_y"):
            schema = wt._CONTENT_PROPS[name]
            assert schema["type"] == "integer"
            assert schema["description"]  # non-empty guidance for the model

    def test_grid_position_not_required(self, provider):
        tools = {t.name: t for t in provider.get_tools()}
        assert "grid_x" not in tools["create_widget"].input_schema["required"]
        assert "grid_x" not in tools["update_widget"].input_schema["required"]
        assert tools["create_widget"].input_schema["required"] == ["title", "data_query", "html"]


class TestCreate:
    def test_persists_widget_into_the_version(self, provider, session, version_id):
        result = provider._create(_create_args(title="Wall", html="<p>"))
        wid = result.host_data["id"]
        widgets = _widgets(session, version_id)
        assert [(w.id, w.title, w.html) for w in widgets] == [(wid, "Wall", "<p>")]

    def test_unplaced_lands_at_top_of_empty_grid(self, provider, session, version_id):
        provider._create(_create_args())
        widget = _widgets(session, version_id)[0]
        assert (widget.grid_x, widget.grid_y) == (0, 0)

    def test_explicit_coords_are_used(self, provider, session, version_id):
        provider._create(_create_args(grid_x=3, grid_y=6))
        widget = _widgets(session, version_id)[0]
        assert (widget.grid_x, widget.grid_y) == (3, 6)

    def test_size_defaults_independent_of_position(self, provider):
        host = provider._create(_create_args()).host_data
        assert (host["grid_w"], host["grid_h"]) == (4, 3)

    def test_second_unplaced_widget_stacks_below_the_first(self, provider, session, version_id):
        provider._create(_create_args(grid_x=0, grid_y=0, grid_h=3))  # occupies rows 0..3
        provider._create(_create_args())  # no coords -> bottom
        assert max(w.grid_y for w in _widgets(session, version_id)) == 3


class TestUpdate:
    def test_merges_content(self, provider, session, version_id):
        wid = provider._create(_create_args(title="Old")).host_data["id"]
        provider._update({"widget_id": wid, "title": "New", "html": "<span>"})
        widget = repo.get_version_widget(session, version_id, wid)
        assert (widget.title, widget.html) == ("New", "<span>")

    def test_coords_move_an_existing_widget(self, provider, session, version_id):
        wid = provider._create(_create_args(grid_x=1, grid_y=1)).host_data["id"]
        provider._update({"widget_id": wid, "grid_x": 0, "grid_y": 9})
        widget = repo.get_version_widget(session, version_id, wid)
        assert (widget.grid_x, widget.grid_y) == (0, 9)

    def test_content_only_edit_keeps_position(self, provider, session, version_id):
        wid = provider._create(_create_args(grid_x=2, grid_y=2)).host_data["id"]
        provider._update({"widget_id": wid, "title": "Renamed"})
        widget = repo.get_version_widget(session, version_id, wid)
        assert widget.title == "Renamed"
        assert (widget.grid_x, widget.grid_y) == (2, 2)

    def test_does_not_create_a_duplicate(self, provider, session, version_id):
        wid = provider._create(_create_args()).host_data["id"]
        provider._update({"widget_id": wid, "title": "Edited"})
        assert len(_widgets(session, version_id)) == 1

    def test_unknown_widget_is_reported_not_created(self, provider, session, version_id):
        result = provider._update({"widget_id": "nope", "title": "x"})
        assert isinstance(result, str) and "not found" in result
        assert _widgets(session, version_id) == []

    def test_missing_widget_id_is_reported(self, provider):
        assert "requires widget_id" in provider._update({})


class TestOptInt:
    def test_coerces_value_or_none(self):
        assert wt._opt_int(None) is None
        assert wt._opt_int(5) == 5
