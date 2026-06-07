"""Unit tests for merge_widget_content (yaffo/page_builder/widget_merge.py).

It produces the "current content" each widget on the client's grid has — stored
content overlaid with the client's unsaved draft fields — that the agent edits
against. Stored widgets are stand-ins exposing the widget attributes.
"""
import pytest

from yaffo.page_builder.widget_merge import merge_widget_content

pytestmark = pytest.mark.unit


class _Stored:
    """A stored widget stand-in (storage-agnostic: any object with the fields)."""
    def __init__(self, **kw):
        self.id = kw["id"]
        self.title = kw.get("title", "Stored")
        self.data_query = kw.get("data_query", {})
        self.state = kw.get("state", {})
        self.html = kw.get("html", "")
        self.css = kw.get("css", "")
        self.js = kw.get("js", "")
        self.grid_x = kw.get("grid_x", 0)
        self.grid_y = kw.get("grid_y", 0)
        self.grid_w = kw.get("grid_w", 4)
        self.grid_h = kw.get("grid_h", 3)


class TestMergeWidgetContent:
    def test_new_draft_uses_client_content_and_defaults(self):
        (merged,) = merge_widget_content([], [{"id": "n", "title": "New", "html": "<div>"}])
        assert merged["title"] == "New"
        assert merged["html"] == "<div>"
        assert merged["css"] == ""            # default fills omitted content
        assert (merged["grid_w"], merged["grid_h"]) == (4, 3)
        assert (merged["grid_x"], merged["grid_y"]) == (0, 0)

    def test_client_draft_fields_win_over_stored(self):
        stored = _Stored(id="w1", html="<old>", title="Old")
        (merged,) = merge_widget_content([stored], [{"id": "w1", "html": "<new>"}])
        assert merged["html"] == "<new>"
        assert merged["title"] == "Old"       # omitted by client -> stored kept

    def test_layout_comes_from_client_xywh(self):
        (merged,) = merge_widget_content([], [{"id": "n", "x": 2, "y": 3, "w": 6, "h": 4}])
        assert (merged["grid_x"], merged["grid_y"], merged["grid_w"], merged["grid_h"]) == (2, 3, 6, 4)

    def test_preserves_client_order(self):
        merged = merge_widget_content([], [{"id": "a"}, {"id": "b"}, {"id": "c"}])
        assert [m["id"] for m in merged] == ["a", "b", "c"]

    def test_no_prompt_field(self):
        # `prompt` was removed from the widget; merge must not resurrect it.
        (merged,) = merge_widget_content([_Stored(id="w1")], [{"id": "w1"}])
        assert "prompt" not in merged