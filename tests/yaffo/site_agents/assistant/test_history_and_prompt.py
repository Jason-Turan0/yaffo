import pytest

from yaffo.site_agents.assistant.history import normalize_turns
from yaffo.site_agents.assistant.prompt_generator.prompt import build_assistant_system_prompt, build_assistant_user_message

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


def test_knowledge_only_prompt_offers_no_diagnostics_or_scripts():
    prompt = build_assistant_system_prompt()
    assert "<diagnostics>" not in prompt and "<scripts>" not in prompt
    assert "You can't see the user's library" in prompt


def test_prompt_lists_only_enabled_groups_and_read_host_functions():
    prompt = build_assistant_system_prompt(frozenset({"logs"}))
    assert "<diagnostics>" in prompt and "recent_errors" in prompt
    assert "list_dir" not in prompt and "<scripts>" not in prompt
    assert "<data>" in prompt  # the untrusted-data rule

    with_scripts = build_assistant_system_prompt(frozenset({"library"}))
    assert "<scripts>" in with_scripts and "data_query(query)" in with_scripts
    assert "tag_media_items" not in with_scripts and "$ref" not in with_scripts


def test_user_message_carries_attached_context():
    message = build_assistant_user_message(
        "Why?", locale="en",
        context={"page": "Utilities <Index>", "error_code": "filesystem_scan_failed", "ignored": "x"})
    assert "<page>Utilities &lt;Index&gt;</page>" in message
    assert "<error_code>filesystem_scan_failed</error_code>" in message
    assert "ignored" not in message

