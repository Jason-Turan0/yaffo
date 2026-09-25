import pytest

from yaffo.site_agents.assistant.history import normalize_turns
from yaffo.site_agents.assistant.prompt import build_assistant_system_prompt, build_assistant_user_message

pytestmark = pytest.mark.unit


def test_normalize_turns_alternates_and_joins_same_side_runs():
    events = [
        ("assistant", "stray greeting"),
        ("user", "first"),
        ("tool", "ignored"),
        ("assistant", "Looking…"),
        ("assistant", "Answer."),
        ("user", "second, whose run failed"),
        ("error", "ignored"),
        ("user", "second again"),
        ("assistant", "  "),
    ]
    assert normalize_turns(events) == [
        ("user", "first"),
        ("assistant", "Looking…\n\nAnswer."),
        ("user", "second, whose run failed\n\nsecond again"),
    ]


def test_normalize_turns_empty():
    assert normalize_turns([]) == []


def test_system_prompt_is_stable_and_carries_the_response_language_rule():
    prompt = build_assistant_system_prompt()
    assert prompt == build_assistant_system_prompt()
    assert "<response_language>" in prompt
    assert "search_docs" in prompt and "read_doc" in prompt
    assert "can't change anything" in prompt


def test_user_message_carries_the_application_locale():
    message = build_assistant_user_message("  Wie füge ich Ordner hinzu?  ", locale="de")
    assert "<application_locale>de</application_locale>" in message
    assert "Wie füge ich Ordner hinzu?" in message
