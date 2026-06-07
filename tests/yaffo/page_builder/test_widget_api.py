"""Tests for the widget-runtime API source and its embedding in the system prompt.

The same widget_api.js source is inlined into each iframe and shown to the model;
these pin that the model-facing API is actually present and named as expected.
"""
import pytest

from yaffo.page_builder.prompt_generator.system_prompt import build_system_prompt
from yaffo.page_builder.widget_api import widget_api_source

pytestmark = pytest.mark.unit

# The public surface widgets (and the model) rely on.
_API_MEMBERS = ("data:", "state:", "query:", "publish:", "subscribe:", "saveState:", "photoUrl:")


class TestWidgetApiSource:
    def test_exposes_the_init_function_and_members(self):
        src = widget_api_source()
        # Installed via the project's init-function convention; the frame assigns
        # the result to window.yaffo.
        assert "window.PHOTO_ORGANIZER.initWidgetApi" in src
        assert "window.yaffo" in src  # documented surface (in the header comment)
        for member in _API_MEMBERS:
            assert member in src, member

    def test_is_cached(self):
        assert widget_api_source() is widget_api_source()


class TestSystemPromptEmbedsApi:
    def test_prompt_advertises_the_yaffo_api(self):
        prompt = build_system_prompt()
        assert "window.PHOTO_ORGANIZER.initWidgetApi" in prompt
        for member in _API_MEMBERS:
            assert member in prompt, member

    def test_old_handrolled_messaging_table_is_gone(self):
        # The hand-written postMessage cheatsheet was replaced by the real source.
        assert "yaffo:query   {requestId, query}" not in build_system_prompt()