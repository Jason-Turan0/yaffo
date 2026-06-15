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
)
from yaffo.db.repositories import automation_repository as repo
from yaffo.page_builder.tool_providers.automation_tool import AutomationToolProvider
from yaffo.page_builder.tool_providers.tool_provider_types import ToolResult

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

    result = tool.call_tool(tool.WRITE, {"code": 'rows = data_query({"source": "photos"})\nprint(len(rows))'})

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
