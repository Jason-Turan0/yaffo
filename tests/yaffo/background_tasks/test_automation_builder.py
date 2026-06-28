"""Unit tests for the automation builder's persistence pieces: the
write_automation_code tool (validates + saves the draft) and the repository's
publish / discard. Run against a throwaway SQLite DB.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import (
    Automation,
    AUTOMATION_STATUS_ACCEPTED,
    AUTOMATION_STATUS_READY,
    MediaItem,
)
from yaffo.db.repositories import automation_repository as repo
from yaffo.db.repositories.media_dir_repository import add_media_dir
from yaffo.site_agents.tool_providers.automation_tool import AutomationToolProvider
from yaffo.site_agents.tool_providers.tool_provider_types import ToolResult

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


def _automation(session, **kw):
    defaults = dict(slug="my-auto", name="My auto", is_system=False, status=AUTOMATION_STATUS_READY)
    defaults.update(kw)
    a = Automation(**defaults)
    session.add(a)
    session.commit()
    return a


def test_tool_saves_valid_code_to_working_draft(session):
    _automation(session)
    tool = AutomationToolProvider("my-auto", session=session)

    result = tool.call_tool(tool.WRITE, {"code": 'rows = data_query({"source": "media_items"})\nprint(len(rows))'})

    assert isinstance(result, ToolResult)
    assert result.host_data["slug"] == "my-auto"
    assert repo.get_by_slug(session, "my-auto").working_code.startswith("rows = data_query")


def test_tool_rejects_unparseable_code(session):
    _automation(session)
    tool = AutomationToolProvider("my-auto", session=session)

    result = tool.call_tool(tool.WRITE, {"code": "def (:"})

    assert isinstance(result, str)
    assert "did not parse" in result
    assert repo.get_by_slug(session, "my-auto").working_code is None


def test_tool_tests_code_non_destructively_against_path(session, tmp_path):
    _automation(session)
    root = (tmp_path / "lib").resolve()
    media_dir = add_media_dir(session, str(root))
    session.add_all([
        MediaItem(id=1, full_file_path=str(root / "2024" / "a.jpg")),
        MediaItem(id=2, full_file_path=str(root / "2024" / "b.jpg")),
        MediaItem(id=3, full_file_path=str(root / "2023" / "c.jpg")),
    ])
    session.commit()
    tool = AutomationToolProvider("my-auto", session=session)

    code = (
        "print(len(ctx['media_item_ids']))\n"
        "tag_media_items([{'media_item_id': pid, 'name': 'tested'} for pid in ctx['media_item_ids']])"
    )
    assert "test_automation_code" in {definition.name for definition in tool.get_tools()}

    result = tool.call_tool("test_automation_code", {
        "code": code,
        "media_dir_id": media_dir.id,
        "path": "2024",
    })

    assert isinstance(result, ToolResult)
    assert result.host_data["success"] is True
    assert result.host_data["media_item_ids"] == [1, 2]
    assert result.host_data["output"] == ["2"]
    assert result.host_data["actions"] == [{
        "summary": "Tag 2 photo(s)",
        "name": "tag_media_items",
        "args": [[
            {"media_item_id": 1, "name": "tested"},
            {"media_item_id": 2, "name": "tested"},
        ]],
    }]
    assert session.query(MediaItem).count() == 3
    assert repo.get_by_slug(session, "my-auto").working_code is None
    assert "Host actions that would have occurred" in result.model_text


def test_publish_copies_working_to_published(session):
    _automation(session, working_code="print('hi')", published_code=None)

    assert repo.publish(session, "my-auto") is True

    a = repo.get_by_slug(session, "my-auto")
    assert a.published_code == "print('hi')"
    assert a.status == AUTOMATION_STATUS_ACCEPTED


def test_publish_with_no_draft_is_noop(session):
    _automation(session, working_code=None, published_code="print('live')")
    assert repo.publish(session, "my-auto") is False
    assert repo.get_by_slug(session, "my-auto").published_code == "print('live')"


def test_discard_drops_working_keeps_published(session):
    _automation(session, working_code="draft", published_code="print('live')")

    repo.discard_draft(session, "my-auto")

    a = repo.get_by_slug(session, "my-auto")
    assert a.working_code is None
    assert a.published_code == "print('live')"
    assert a.status == AUTOMATION_STATUS_ACCEPTED
