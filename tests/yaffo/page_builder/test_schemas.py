"""Contract tests for the page-builder wire schemas (yaffo/page_builder/schemas.py).

The browser WidgetDraft is deliberately the persisted Widget *minus* its
persistence/internal columns (Python has no static Omit<T, K>, so the relationship
is pinned here). If a column is added to Widget, the first test fails and forces a
decision: expose it on the draft or add it to the omit set.
"""
import dataclasses

import pytest

from yaffo.db.models import Widget
from yaffo.page_builder import schemas
from yaffo.page_builder.schemas import WidgetDraft

pytestmark = pytest.mark.unit

# Widget columns the browser draft intentionally drops: the foreign key and the
# persistence status. WidgetDraft == Widget - these.
_OMITTED_WIDGET_COLUMNS = {"page_id", "status"}


class TestWidgetDraftMatchesModel:
    def test_fields_are_widget_columns_minus_persistence(self):
        model_columns = {c.name for c in Widget.__table__.columns}
        draft_fields = {f.name for f in dataclasses.fields(WidgetDraft)}
        assert draft_fields == model_columns - _OMITTED_WIDGET_COLUMNS

    def test_omitted_columns_actually_exist_on_the_model(self):
        # Keeps the omit set honest: a renamed/removed column can't linger here.
        model_columns = {c.name for c in Widget.__table__.columns}
        assert _OMITTED_WIDGET_COLUMNS <= model_columns


class TestChatRecords:
    def test_record_event_tags(self):
        assert schemas.chat_message("hi") == {"event": "message", "content": "hi"}
        assert schemas.chat_status("Working…") == {"event": "status", "text": "Working…"}
        assert schemas.chat_widget_new({"id": "w"}) == {"event": "widget_new", "widget": {"id": "w"}}
        assert schemas.chat_widget_updated({"id": "w"}) == {"event": "widget_updated", "widget": {"id": "w"}}
        assert schemas.chat_done() == {"event": "done"}