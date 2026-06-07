"""Unit tests for the widget tools (yaffo/page_builder/tool_providers/widget_tool.py).

Focus: the model-facing schema (derived from WidgetDraft) and grid placement —
the model may position a widget via grid_x/grid_y, and omitting them means "bottom"
on create / "leave it" on edit (signaled to the client as null).
"""
import pytest

from yaffo.page_builder.tool_providers import widget_tool as wt

pytestmark = pytest.mark.unit


def _create_args(**over):
    args = {"title": "A", "data_query": {}, "html": "<div>"}
    args.update(over)
    return args


class TestSchema:
    def test_content_fields_include_grid_position(self):
        assert "grid_x" in wt._CONTENT_FIELDS
        assert "grid_y" in wt._CONTENT_FIELDS

    def test_grid_position_is_optional_integer_with_help(self):
        for name in ("grid_x", "grid_y"):
            schema = wt._CONTENT_PROPS[name]
            assert schema["type"] == "integer"
            assert schema["description"]  # non-empty guidance for the model

    def test_grid_position_not_required(self):
        tools = {t.name: t for t in wt.WidgetToolProvider(page_id=1).get_tools()}
        assert "grid_x" not in tools["create_widget"].input_schema["required"]
        assert "grid_x" not in tools["update_widget"].input_schema["required"]
        # The original required set is unchanged.
        assert tools["create_widget"].input_schema["required"] == ["title", "data_query", "html"]


class TestCreatePlacement:
    def test_omitted_coords_signal_bottom_as_null(self):
        prov = wt.WidgetToolProvider(page_id=1)
        host = prov._create(_create_args()).host_data
        assert host["grid_x"] is None
        assert host["grid_y"] is None

    def test_explicit_coords_are_used(self):
        prov = wt.WidgetToolProvider(page_id=1)
        host = prov._create(_create_args(grid_x=3, grid_y=6)).host_data
        assert (host["grid_x"], host["grid_y"]) == (3, 6)

    def test_size_defaults_independent_of_position(self):
        prov = wt.WidgetToolProvider(page_id=1)
        host = prov._create(_create_args()).host_data
        assert (host["grid_w"], host["grid_h"]) == (4, 3)


class TestUpdateMove:
    def test_coords_move_an_existing_draft(self):
        prov = wt.WidgetToolProvider(page_id=1)
        wid = prov._create(_create_args(grid_x=1, grid_y=1)).host_data["id"]
        host = prov._update({"widget_id": wid, "grid_x": 0, "grid_y": 9}).host_data
        assert (host["grid_x"], host["grid_y"]) == (0, 9)

    def test_content_only_edit_does_not_move(self):
        # A widget with no explicit placement, edited for content only, keeps the
        # null placement signal (the client leaves it where it is).
        prov = wt.WidgetToolProvider(page_id=1)
        wid = prov._create(_create_args()).host_data["id"]
        host = prov._update({"widget_id": wid, "title": "Renamed"}).host_data
        assert host["title"] == "Renamed"
        assert host["grid_x"] is None
        assert host["grid_y"] is None

    def test_seeded_widget_edit_leaves_position_null(self):
        # Seeded from the page's current widgets (content for merge); editing
        # without coords must not invent a position.
        prov = wt.WidgetToolProvider(
            page_id=1,
            current_widgets=[{"id": "w1", "title": "Old", "html": "<div>"}],
        )
        host = prov._update({"widget_id": "w1", "html": "<span>"}).host_data
        assert host["html"] == "<span>"
        assert host["grid_x"] is None
        assert host["grid_y"] is None


class TestOptInt:
    def test_coerces_value_or_none(self):
        assert wt._opt_int(None) is None
        assert wt._opt_int(5) == 5
        assert wt._opt_int("7") == 7  # schema sends ints; be tolerant anyway