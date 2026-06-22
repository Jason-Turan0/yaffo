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
    PAGE_VERSION_STATUS_ACCEPTED,
    PAGE_VERSION_STATUS_FAILED,
    PAGE_VERSION_STATUS_IN_PROGRESS,
    Conversation,
    CustomPage,
    PageVersion,
    Widget,
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
    return repo.create_page(session, "My Trip", subtitle="maine summer")


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
        assert page.subtitle == ""
        assert page.show_title is True
        assert page.widgets == []
        assert page.messages == []

    def test_creates_initial_accepted_published_version(self, session):
        page = repo.create_page(session, "Trip")
        assert page.published_version_id is not None
        assert page.working_version_id is None
        assert page.published_version.status == PAGE_VERSION_STATUS_ACCEPTED
        # Exactly one version exists for a fresh page.
        assert session.query(PageVersion).count() == 1

    def test_is_readable_back(self, session):
        page = repo.create_page(session, "Trip", subtitle="summer")
        fetched = repo.get_page(session, page.id)
        assert fetched.id == page.id
        assert fetched.subtitle == "summer"


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

    def test_orders_by_tab_order(self, session):
        # New pages land at the end of the strip in creation order.
        repo.create_page(session, "First")
        repo.create_page(session, "Second")
        assert [p.title for p in repo.list_pages(session)] == ["First", "Second"]

    def test_tab_order_displaces_and_stays_contiguous(self, session):
        a = repo.create_page(session, "A")
        repo.create_page(session, "B")
        repo.create_page(session, "C")
        # Move A into C's slot; B and C shift up, numbers stay unique 1..N.
        repo.update_page(session, a.id, tab_order=3)
        pages = repo.list_pages(session)
        assert [(p.title, p.tab_order) for p in pages] == [("B", 1), ("C", 2), ("A", 3)]

    def test_tab_order_clamps_out_of_range(self, session):
        a = repo.create_page(session, "A")
        repo.create_page(session, "B")
        # A target past the end just lands the page last; 0/negative lands it first.
        repo.update_page(session, a.id, tab_order=99)
        assert [p.title for p in repo.list_pages(session)] == ["B", "A"]
        repo.update_page(session, a.id, tab_order=0)
        assert [p.title for p in repo.list_pages(session)] == ["A", "B"]


class TestUpdatePage:
    def test_missing_returns_none(self, session):
        assert repo.update_page(session, 999, title="x") is None

    def test_updates_only_provided_fields(self, session, page):
        repo.update_page(session, page.id, title="Renamed")
        fetched = repo.get_page(session, page.id)
        assert fetched.title == "Renamed"
        assert fetched.subtitle == "maine summer"  # untouched
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
        assert [(m.type, m.content) for m in messages] == [
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


class TestSetVersionWidgetState:
    def test_persists_on_the_given_version_only(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id)  # copies a, b
        repo.set_version_widget_state(session, version.id, "a", {"filter": "Camden"})
        assert repo.get_version_widget(session, version.id, "a").state == {"filter": "Camden"}
        # The published version's widget is untouched.
        assert repo.get_widget(session, populated_page.id, "a").state == {}

    def test_missing_widget_is_noop(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id)
        repo.set_version_widget_state(session, version.id, "nope", {"x": 1})  # does not raise


class TestRemoveWidget:
    def test_removes_widget(self, session, page):
        repo.save_page_widgets(session, page.id, [_widget_item("a"), _widget_item("b")])
        repo.remove_widget(session, page.id, "a")
        assert [w.id for w in repo.get_page(session, page.id).widgets] == ["b"]

    def test_missing_widget_is_noop(self, session, page):
        repo.remove_widget(session, page.id, "nope")  # does not raise


class TestRemoveVersionWidget:
    def test_removes_from_the_given_version_only(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id)  # copies a, b
        repo.remove_version_widget(session, version.id, "a")
        assert [w.id for w in repo.get_version(session, version.id).widgets] == ["b"]
        # The published version still has both.
        assert [w.id for w in repo.get_page(session, populated_page.id).widgets] == ["a", "b"]

    def test_missing_widget_is_noop(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id)
        repo.remove_version_widget(session, version.id, "nope")  # does not raise


class TestSavePageWidgets:
    def test_missing_page_is_noop(self, session):
        repo.save_page_widgets(session, 999, [_widget_item("a")])  # does not raise

    def test_creates_widgets_with_layout_and_content(self, session, page):
        repo.save_page_widgets(session, page.id, [
            _widget_item("a", title="Wall", html="<div>", w=4, h=4,
                         data_query={"q": {"source": "media_items"}}),
        ])
        widget = repo.get_widget(session, page.id, "a")
        assert widget.title == "Wall"
        assert widget.html == "<div>"
        assert widget.grid_w == 4 and widget.grid_h == 4
        assert widget.data_query == {"q": {"source": "media_items"}}

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

    def test_writes_into_the_published_version(self, session, page):
        # Manual edits land on the live (published) version, not a new one.
        repo.save_page_widgets(session, page.id, [_widget_item("a")])
        assert session.query(PageVersion).count() == 1
        published = repo.get_page(session, page.id).published_version
        assert [w.id for w in published.widgets] == ["a"]


@pytest.fixture
def populated_page(session):
    """A published page with two widgets and a user/assistant exchange."""
    page = repo.create_page(session, "Trip", subtitle="maine summer")
    repo.add_message(session, page.id, "user", "make a wall")
    repo.add_message(session, page.id, "assistant", "done")
    repo.save_page_widgets(session, page.id, [
        _widget_item("a", title="A", html="<p>a</p>", data_query={"q": {"source": "media_items"}}),
        _widget_item("b", title="B", html="<p>b</p>"),
    ])
    return repo.get_page(session, page.id)


class TestForkVersion:
    def test_missing_page_returns_none(self, session):
        assert repo.fork_version(session, 999) is None

    def test_creates_in_progress_working_version(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id)
        assert version.status == PAGE_VERSION_STATUS_IN_PROGRESS
        assert version.started_at is not None
        assert version.parent_version_id == populated_page.published_version_id
        page = repo.get_page(session, populated_page.id)
        assert page.working_version_id == version.id
        # Published pointer is untouched -- presentation still shows the old version.
        assert page.published_version_id == populated_page.published_version_id

    def test_snapshots_published_widgets_when_no_client_list(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id)
        forked = repo.get_version(session, version.id)
        assert [(w.id, w.title, w.html) for w in forked.widgets] == [
            ("a", "A", "<p>a</p>"),
            ("b", "B", "<p>b</p>"),
        ]
        assert forked.widgets[0].data_query == {"q": {"source": "media_items"}}

    def test_copies_prior_conversation(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id)
        forked = repo.get_version(session, version.id)
        assert [(m.type, m.content) for m in forked.messages] == [
            ("user", "make a wall"),
            ("assistant", "done"),
        ]

    def test_client_list_overlays_unsaved_edits_and_drops_omitted(self, session, populated_page):
        # The client shows an edited "a" (new title) and a manually-added "c", and
        # has dropped "b" -- the fork must capture exactly that set.
        version = repo.fork_version(session, populated_page.id, widgets=[
            {"id": "a", "title": "A edited", "x": 0, "y": 0, "w": 4, "h": 3},
            _widget_item("c", title="C"),
        ])
        forked = repo.get_version(session, version.id)
        assert {w.id: w.title for w in forked.widgets} == {"a": "A edited", "c": "C"}
        # Omitted content (a's html) is preserved from the published snapshot.
        assert next(w for w in forked.widgets if w.id == "a").html == "<p>a</p>"

    def test_forked_widgets_do_not_share_json_with_published(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id)
        forked = repo.get_version(session, version.id)
        published = repo.get_page(session, populated_page.id).published_version
        assert forked.widgets[0].data_query is not published.widgets[0].data_query


class TestPublishVersion:
    def test_missing_version_returns_none(self, session):
        assert repo.publish_version(session, 999) is None

    def test_accepts_and_publishes_clearing_working(self, session, populated_page):
        old_published_id = populated_page.published_version_id
        version = repo.fork_version(session, populated_page.id)
        repo.publish_version(session, version.id)
        page = repo.get_page(session, populated_page.id)
        assert page.published_version_id == version.id
        assert page.working_version_id is None
        assert repo.get_version(session, version.id).status == PAGE_VERSION_STATUS_ACCEPTED

    def test_drops_the_superseded_published_version(self, session, populated_page):
        old_published_id = populated_page.published_version_id
        version = repo.fork_version(session, populated_page.id)
        repo.publish_version(session, version.id)
        assert session.get(PageVersion, old_published_id) is None
        assert session.query(PageVersion).count() == 1

    def test_presentation_reads_published_widgets_after_save(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id, widgets=[_widget_item("z", title="Z")])
        repo.publish_version(session, version.id)
        assert [w.id for w in repo.get_page(session, populated_page.id).widgets] == ["z"]


class TestDeleteVersion:
    def test_missing_version_is_noop(self, session):
        repo.delete_version(session, 999)  # does not raise

    def test_deletes_working_version_and_reverts(self, session, populated_page):
        old_published_id = populated_page.published_version_id
        version = repo.fork_version(session, populated_page.id)
        repo.delete_version(session, version.id)
        page = repo.get_page(session, populated_page.id)
        assert page.working_version_id is None
        # Published pointer is untouched -- the UI snaps back to it.
        assert page.published_version_id == old_published_id
        assert session.get(PageVersion, version.id) is None

    def test_cascades_widgets_and_messages(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id)
        # Two widgets + two messages were copied into the working version.
        before = session.query(Widget).count() + session.query(Conversation).count()
        repo.delete_version(session, version.id)
        # Only the published version's widgets + messages remain.
        assert session.query(Widget).count() == 2
        assert session.query(Conversation).count() == 2
        assert before == 8  # 2 published + 2 forked, of each


class TestSetVersionStatus:
    def test_missing_version_returns_none(self, session):
        assert repo.set_version_status(session, 999, PAGE_VERSION_STATUS_FAILED) is None

    def test_transitions_and_records_error(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id)
        repo.set_version_status(
            session, version.id, PAGE_VERSION_STATUS_FAILED, error="boom", completed=True
        )
        fetched = repo.get_version(session, version.id)
        assert fetched.status == PAGE_VERSION_STATUS_FAILED
        assert fetched.error == "boom"
        assert fetched.completed_at is not None


class TestRestartVersion:
    def test_missing_version_returns_none(self, session):
        assert repo.restart_version(session, 999) is None

    def test_resets_to_in_progress_and_clears_timing(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id)
        repo.set_version_status(
            session, version.id, PAGE_VERSION_STATUS_FAILED, error="boom", completed=True
        )
        repo.restart_version(session, version.id)
        fetched = repo.get_version(session, version.id)
        assert fetched.status == PAGE_VERSION_STATUS_IN_PROGRESS
        assert fetched.error is None
        assert fetched.completed_at is None
        assert fetched.started_at is not None


class TestSaveVersionWidgets:
    def test_missing_version_is_noop(self, session):
        repo.save_version_widgets(session, 999, [{"id": "x"}])  # does not raise

    def test_writes_widgets_into_the_given_version(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id)
        repo.save_version_widgets(session, version.id, [
            _widget_item("x", title="X"),
        ])
        assert [(w.id, w.title) for w in repo.get_version(session, version.id).widgets] == [("x", "X")]

    def test_does_not_touch_other_versions(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id)
        repo.save_version_widgets(session, version.id, [])  # clear the fork only
        assert repo.get_version(session, version.id).widgets == []
        # The published version's widgets are untouched.
        assert [w.id for w in repo.get_page(session, populated_page.id).widgets] == ["a", "b"]


class TestAddVersionMessage:
    def test_missing_version_returns_none(self, session):
        assert repo.add_version_message(session, 999, "status", "hi") is None

    def test_appends_to_the_version_not_the_published_feed(self, session, populated_page):
        version = repo.fork_version(session, populated_page.id)
        repo.add_version_message(session, version.id, "status", "Creating widget…")
        repo.add_version_message(session, version.id, "error", "nope")
        forked = repo.get_version(session, version.id)
        # The two copied messages plus the two new annotations.
        assert [(m.type, m.content) for m in forked.messages][-2:] == [
            ("status", "Creating widget…"),
            ("error", "nope"),
        ]
        # The published feed is unaffected.
        assert len(repo.get_page(session, populated_page.id).messages) == 2