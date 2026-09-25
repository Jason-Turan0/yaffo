import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import (
    ASSISTANT_STATUS_IDLE,
    ASSISTANT_STATUS_RUNNING,
    AssistantConversation,
    AssistantEvent,
)
from yaffo.db.repositories import assistant_repository as repo

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess


def test_title_from_message_trims_on_a_word_boundary():
    assert repo.title_from_message("  How   do I\nadd folders? ") == "How do I add folders?"
    long = "word " * 30
    title = repo.title_from_message(long)
    assert title.endswith("…") and len(title) <= repo.TITLE_MAX_LENGTH + 1
    assert not title[:-1].endswith(" ")


def test_events_get_sequential_seq_and_payload_json(session):
    conversation = repo.create_conversation(session, "Q")
    first = repo.add_event(session, conversation.id, "user", "hi")
    second = repo.add_event(session, conversation.id, "tool", "", {"tool": "search_docs"})
    assert (first.seq, second.seq) == (0, 1)
    assert json.loads(second.payload) == {"tool": "search_docs"}
    assert [e.seq for e in repo.list_events(session, conversation.id, after_seq=0)] == [1]


def test_model_turns_are_user_and_assistant_only(session):
    conversation = repo.create_conversation(session, "Q")
    for kind, content in [("user", "q"), ("tool", ""), ("assistant", "a"), ("error", "x")]:
        repo.add_event(session, conversation.id, kind, content)
    assert repo.model_turns(session, conversation.id) == [("user", "q"), ("assistant", "a")]


def test_start_run_refuses_a_second_concurrent_run(session):
    conversation = repo.create_conversation(session, "Q")
    assert repo.start_run(session, conversation.id, "m") is True
    assert repo.start_run(session, conversation.id, "m") is False
    refreshed = repo.get_conversation(session, conversation.id)
    assert refreshed.status == ASSISTANT_STATUS_RUNNING
    assert refreshed.model_id == "m" and refreshed.run_started_at is not None
    repo.set_status(session, conversation.id, ASSISTANT_STATUS_IDLE)
    assert repo.start_run(session, conversation.id, "m") is True


def test_list_is_most_recent_first_and_delete_removes_events(session):
    older = repo.create_conversation(session, "older")
    newer = repo.create_conversation(session, "newer")
    repo.add_event(session, older.id, "user", "bump")  # activity moves it to the top
    assert [c.title for c in repo.list_conversations(session)] == ["older", "newer"]
    assert repo.count_conversations(session) == 2

    assert repo.delete_conversation(session, older.id) is True
    assert session.query(AssistantEvent).count() == 0
    assert repo.delete_conversation(session, older.id) is False
    assert repo.delete_all_conversations(session) == 1
    assert session.query(AssistantConversation).count() == 0


def test_rename(session):
    conversation = repo.create_conversation(session, "old")
    assert repo.rename_conversation(session, conversation.id, "new") is True
    assert repo.get_conversation(session, conversation.id).title == "new"
