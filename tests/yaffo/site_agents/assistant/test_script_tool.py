"""run_script: read-only Starlark over the library in the real sandbox."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.starlark_runner import RunLimits
from yaffo.db import db
from yaffo.db.models import FACE_STATUS_ASSIGNED, FACE_STATUS_UNASSIGNED, Face, MediaItem, Person, PersonFace, Tag
from yaffo.site_agents.assistant.prompt_generator.prompt import ONLY_PERSON_EXAMPLE
from yaffo.site_agents.assistant.redact import Redactor
from yaffo.site_agents.assistant.tool_providers.script_tool import DESCRIBE_SOURCE, RUN_SCRIPT, ScriptToolProvider

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lib.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            MediaItem(id=i, full_file_path=f"/Users/alex/Pictures/{i}.jpg", year=2019 if i < 3 else 2020,
                      media_type="photo", status="INDEXED")
            for i in range(1, 5)
        ])
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def provider(session):
    return ScriptToolProvider(session, redactor=Redactor(home=Path("/Users/alex")),
                              limits=RunLimits(timeout_seconds=10, max_host_calls=20))


def _run(provider, code, purpose="Count photos"):
    return provider.call_tool(RUN_SCRIPT, {"code": code, "purpose": purpose})


def test_reads_live_data_and_returns_the_value(provider):
    result = _run(provider, 'rows = data_query({"source": "media_items", "year": {"eq": 2019}})\nlen(rows)')
    assert "Value: 2" in result.model_text
    activity = result.host_data
    assert activity["tool"] == RUN_SCRIPT and activity["purpose"] == "Count photos"
    assert "data_query" in activity["script"] and activity["error"] is False


def test_output_is_redacted(provider):
    result = _run(provider, 'print("error at /Users/alex/Pictures/1.jpg")\nNone')
    assert "~/Pictures/1.jpg" in result.model_text
    assert "/Users/alex" not in result.model_text and "/Users/alex" not in result.host_data["detail"]


def test_mutating_functions_are_not_bound(provider, session):
    result = _run(provider, 'tag_media_items([{"media_item_id": 1, "name": "x"}])')
    assert "Error:" in result.model_text and result.host_data["error"] is True
    assert session.query(Tag).count() == 0


def test_errors_come_back_as_data(provider):
    result = _run(provider, "1 +")
    assert result.model_text.startswith('<data source="run_script">\nError:')


def test_runaway_script_is_stopped(provider):
    provider.limits = RunLimits(timeout_seconds=1)
    result = _run(provider, "def f():\n    n = 0\n    for i in range(100000000):\n        n += i\n    return n\nf()")
    assert result.host_data["error"] is True


def test_empty_script_and_purpose_are_trimmed(provider):
    assert _run(provider, "   ").host_data["error"] is True
    assert _run(provider, "1", purpose="  a   b  ").host_data["purpose"] == "a b"


def test_describe_data_source(provider):
    result = provider.call_tool(DESCRIBE_SOURCE, {"source": "media_items"})
    assert '"source": "media_items"' in result.model_text and result.host_data["title"] == "media_items"
    assert provider.call_tool(DESCRIBE_SOURCE, {"source": "nope"}).host_data["error"] is True


def test_the_prompts_worked_example_runs_and_finds_photos_of_only_that_person(provider, session):
    """The example in the system prompt's <starlark> block must stay valid Starlark
    against the real data_query: photo 1 has only person 10, photo 2 has 10 and 11,
    photo 3 has 10 and an unassigned face, photo 4 has only 11."""
    session.add_all([Person(id=10, name="Billy"), Person(id=11, name="Bea")])
    faces = [(1, 1, 10), (2, 2, 10), (3, 2, 11), (4, 3, 10), (5, 3, None), (6, 4, 11)]
    for face_id, media_item_id, person_id in faces:
        session.add(Face(id=face_id, media_item_id=media_item_id,
                         status=FACE_STATUS_ASSIGNED if person_id else FACE_STATUS_UNASSIGNED))
        if person_id:
            session.add(PersonFace(face_id=face_id, person_id=person_id))
    session.commit()

    result = _run(provider, ONLY_PERSON_EXAMPLE + "\nonly", purpose="Only Billy")

    assert result.host_data["error"] is False, result.model_text
    assert "Value: [1, 3]" in result.model_text
