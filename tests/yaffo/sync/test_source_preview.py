"""The Test button for an import sync: it lists the source a page at a time within a
time budget and counts what a run would do with every file it saw."""
from pathlib import Path

import pytest

from yaffo.sync import folder_source, preview, setup
from yaffo.sync.preview import RECORD_NEW, preview_sync

from .conftest import MEDIA_DIR_ID, write_jpeg

pytestmark = pytest.mark.unit


@pytest.fixture
def card(tmp_path) -> Path:
    folder = tmp_path / "card"
    for index in range(12):
        write_jpeg(folder / f"IMG_{index:04d}.JPG", color=(index * 20, 20, 30))
    return folder


@pytest.fixture
def source(session, library, card, monkeypatch):
    monkeypatch.setattr(folder_source, "PAGE_SIZE", 1)
    automation = setup.create_sync(session, "Camera card", adapter_id=1)
    setup.set_options(session, automation, {"folder": str(card)})
    setup.set_import_root(session, automation, MEDIA_DIR_ID, "Imports/Camera", "year_month")
    return automation


def test_counts_every_page_of_the_source_and_pages_through_them(session, source):
    result = preview_sync(session, source, page=1, page_size=10)

    assert result.problem is None
    assert result.total_items == 12 and result.state_counts == {RECORD_NEW: 12} and result.would_add == 12
    assert result.more is False and result.source_total is None
    assert result.current_page == 1
    assert [r.filename for r in result.records] == ["IMG_0010.JPG", "IMG_0011.JPG"]


def test_stops_listing_when_the_time_budget_runs_out(session, source, monkeypatch):
    monkeypatch.setattr(preview, "SOURCE_LIST_BUDGET_SECONDS", 0)

    result = preview_sync(session, source)

    assert result.total_items == 1 and result.would_add == 1
    assert result.more is True


def test_reports_the_total_the_source_gives(session, source, monkeypatch):
    monkeypatch.setattr(preview, "SOURCE_LIST_BUDGET_SECONDS", 0)
    list_page = folder_source.FolderSource.list_page

    def with_total(self, cursor, ctx):
        return {**list_page(self, cursor, ctx), "progress": {"done": 1, "total": 3}}

    monkeypatch.setattr(folder_source.FolderSource, "list_page", with_total)

    result = preview_sync(session, source)

    assert result.total_items == 1 and result.more is True and result.source_total == 3
