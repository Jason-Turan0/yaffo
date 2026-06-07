"""Unit tests for the AI page builder CRUD repository.

Exercises the repository end to end against a real (throwaway) SQLite database so
the model mapping, eager-loaded joins, the page-scoped widget lookup, and the
save_page_widgets reconcile (add / edit / delete / reorder) are all covered.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import (
    Conversation,
    CustomPage,
    Widget,
    WIDGET_STATUS_READY,
)
from yaffo.db.repositories import custom_page_repository as repo

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path):
    """A throwaway SQLite database (temp file) with the real model tables."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


@pytest.fixture
def page(session):
    """A saved, empty page to hang widgets / messages off of."""
    return repo.create_page(session, "My Trip", theme_prompt="maine summer")


def _widget_item(wid=None, **overrides):
    """A client widget entry (the shape Save posts). Layout uses x/y/w/h."""
    item = {"title": "Wall", "html": "<div>", "x": 0, "y": 0, "w": 4, "h": 3}
    if wid is not None:
        item["id"] = wid
    item.update(overrides)
    return item


class TestNewWidgetId:
    def test_returns_unique_hex_guids(self):
        ids = {repo.new_widget_id() for _ in range(100)}
        assert len(ids) == 100
        assert all(len(i) == 32 and int(i, 16) >= 0 for i in ids)


class TestCreatePage:
    def test_persists_with_defaults(self, session):
        page = repo.create_page(session, "Trip")
        assert page.id is not None
        assert page.title == "Trip"
        assert page.theme_prompt == ""
        assert page.show_title is True
        assert page.widgets == []
        assert page.messages == []

    def test_is_readable_back(self, session):
        page = repo.create_page(session, "Trip", theme_prompt="summer")
        fetched = repo.get_page(session, page.id)
        assert fetched.id == page.id
        assert fetched.theme_prompt == "summer"


class TestGetPage:
    def test_missing_returns_none(self, session):
        assert repo.get_page(session, 999) is None

    def test_eager_loads_widgets_ordered_and_messages(self, session, page):
        repo.add_message(session, page.id, "user", "hi")
        repo.save_page_widgets(session, page.id, [
            _widget_item("a", title="A"),
            _widget_item("b", title="B"),
        ])
        # New session so nothing is identity-mapped: proves the join is eager.
        fetched = repo.get_page(session, page.id)
        session.expunge_all()
        assert [w.title for w in fetched.widgets] == ["A", "B"]
        assert [m.content for m in fetched.messages] == ["hi"]


class TestListPages:
    def test_empty(self, session):
        assert repo.list_pages(session) == []

    def test_orders_by_updated_at_desc(self, session):
        first = repo.create_page(session, "First")
        second = repo.create_page(session, "Second")
        # Touch the first so it becomes the most recently updated.
        repo.update_page(session, first.id, title="First (edited)")
        titles = [p.title for p in repo.list_pages(session)]
        assert titles == ["First (edited)", "Second"]


class TestUpdatePage:
    def test_missing_returns_none(self, session):
        assert repo.update_page(session, 999, title="x") is None

    def test_updates_only_provided_fields(self, session, page):
        repo.update_page(session, page.id, title="Renamed")
        fetched = repo.get_page(session, page.id)
        assert fetched.title == "Renamed"
        assert fetched.theme_prompt == "maine summer"  # untouched
        assert fetched.show_title is True

    def test_can_clear_show_title(self, session, page):
        repo.update_page(session, page.id, show_title=False)
        assert repo.get_page(session, page.id).show_title is False


class TestDeletePage:
    def test_missing_is_noop(self, session):
        repo.delete_page(session, 999)  # does not raise

    def test_cascades_to_widgets_and_messages(self, session, page):
        repo.add_message(session, page.id, "user", "hi")
        repo.save_page_widgets(session, page.id, [_widget_item("a")])
        repo.delete_page(session, page.id)
        assert repo.get_page(session, page.id) is None
        assert session.query(Widget).count() == 0
        assert session.query(Conversation).count() == 0


class TestAddMessage:
    def test_missing_page_returns_none(self, session):
        assert repo.add_message(session, 999, "user", "hi") is None

    def test_appends_in_order(self, session, page):
        repo.add_message(session, page.id, "user", "one")
        repo.add_message(session, page.id, "assistant", "two")
        messages = repo.get_page(session, page.id).messages
        assert [(m.role, m.content) for m in messages] == [
            ("user", "one"),
            ("assistant", "two"),
        ]


class TestGetWidget:
    def test_found_within_page(self, session, page):
        repo.save_page_widgets(session, page.id, [_widget_item("a")])
        widget = repo.get_widget(session, page.id, "a")
        assert widget is not None
        assert widget.id == "a"

    def test_missing_widget_returns_none(self, session, page):
        assert repo.get_widget(session, page.id, "nope") is None

    def test_scoped_to_page(self, session, page):
        other = repo.create_page(session, "Other")
        repo.save_page_widgets(session, page.id, [_widget_item("a")])
        # The widget exists, but not on this page.
        assert repo.get_widget(session, other.id, "a") is None


class TestSetWidgetState:
    def test_persists_state(self, session, page):
        repo.save_page_widgets(session, page.id, [_widget_item("a")])
        repo.set_widget_state(session, page.id, "a", {"filter": "Camden"})
        assert repo.get_widget(session, page.id, "a").state == {"filter": "Camden"}

    def test_none_becomes_empty_dict(self, session, page):
        repo.save_page_widgets(session, page.id, [_widget_item("a")])
        repo.set_widget_state(session, page.id, "a", None)
        assert repo.get_widget(session, page.id, "a").state == {}

    def test_missing_widget_is_noop(self, session, page):
        repo.set_widget_state(session, page.id, "nope", {"x": 1})  # does not raise


class TestRemoveWidget:
    def test_removes_widget(self, session, page):
        repo.save_page_widgets(session, page.id, [_widget_item("a"), _widget_item("b")])
        repo.remove_widget(session, page.id, "a")
        assert [w.id for w in repo.get_page(session, page.id).widgets] == ["b"]

    def test_missing_widget_is_noop(self, session, page):
        repo.remove_widget(session, page.id, "nope")  # does not raise


class TestSavePageWidgets:
    def test_missing_page_is_noop(self, session):
        repo.save_page_widgets(session, 999, [_widget_item("a")])  # does not raise

    def test_creates_widgets_with_layout_and_content(self, session, page):
        repo.save_page_widgets(session, page.id, [
            _widget_item("a", title="Wall", html="<div>", w=4, h=4,
                         data_query={"q": {"source": "photos"}}),
        ])
        widget = repo.get_widget(session, page.id, "a")
        assert widget.title == "Wall"
        assert widget.html == "<div>"
        assert widget.grid_w == 4 and widget.grid_h == 4
        assert widget.data_query == {"q": {"source": "photos"}}
        assert widget.status == WIDGET_STATUS_READY

    def test_mints_id_when_absent(self, session, page):
        repo.save_page_widgets(session, page.id, [_widget_item()])  # no id
        widgets = repo.get_page(session, page.id).widgets
        assert len(widgets) == 1
        assert len(widgets[0].id) == 32  # minted GUID

    def test_widgets_returned_in_grid_reading_order(self, session, page):
        # No separate position column — get_page orders by grid coords (top-to-
        # bottom, then left-to-right), regardless of save order.
        repo.save_page_widgets(session, page.id, [
            _widget_item("bottom", x=0, y=5),
            _widget_item("top_right", x=4, y=0),
            _widget_item("top_left", x=0, y=0),
        ])
        widgets = repo.get_page(session, page.id).widgets
        assert [w.id for w in widgets] == ["top_left", "top_right", "bottom"]

    def test_layout_only_entry_keeps_stored_content(self, session, page):
        repo.save_page_widgets(session, page.id, [
            _widget_item("a", title="Original", html="<p>kept</p>"),
        ])
        # Re-save with layout only (no content keys) -- content must survive.
        repo.save_page_widgets(session, page.id, [{"id": "a", "x": 1, "y": 1, "w": 8, "h": 8}])
        widget = repo.get_widget(session, page.id, "a")
        assert widget.title == "Original"
        assert widget.html == "<p>kept</p>"
        assert widget.grid_w == 8

    def test_edits_content_of_existing_widget(self, session, page):
        repo.save_page_widgets(session, page.id, [_widget_item("a", title="Old")])
        repo.save_page_widgets(session, page.id, [_widget_item("a", title="New", html="<span>")])
        widget = repo.get_widget(session, page.id, "a")
        assert widget.title == "New"
        assert widget.html == "<span>"

    def test_drops_widgets_absent_from_payload(self, session, page):
        repo.save_page_widgets(session, page.id, [_widget_item("a"), _widget_item("b")])
        repo.save_page_widgets(session, page.id, [_widget_item("a")])  # b omitted
        assert [w.id for w in repo.get_page(session, page.id).widgets] == ["a"]
        assert session.query(Widget).count() == 1

    def test_empty_payload_clears_all_widgets(self, session, page):
        repo.save_page_widgets(session, page.id, [_widget_item("a")])
        repo.save_page_widgets(session, page.id, [])
        assert repo.get_page(session, page.id).widgets == []

    def test_json_none_coerced_to_empty_dict(self, session, page):
        repo.save_page_widgets(session, page.id, [_widget_item("a", data_query=None, state=None)])
        widget = repo.get_widget(session, page.id, "a")
        assert widget.data_query == {}
        assert widget.state == {}