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



def _events(*rows):
    """(seq, kind, content, payload) rows as stored events."""
    import json
    from types import SimpleNamespace
    return [SimpleNamespace(seq=seq, kind=kind, content=content,
                            payload=json.dumps(payload) if payload is not None else None)
            for seq, kind, content, payload in rows]


def test_follow_up_turns_keep_the_earlier_context_redacted_and_escaped():
    from yaffo.site_agents.assistant.history import transcript_turns
    context = {"page": "/utilities/index-photos", "job_id": "job-7",
               "error": "Could not read /Volumes/Photos/<a>.jpg"}
    events = _events(
        (0, "user", "What went wrong here?", {"context": context}),
        (1, "assistant", "Seven files failed.", None),
        (2, "user", "Can you retry them?", None),
    )
    redact = lambda text: text.replace("/Volumes/Photos", "[media folder m1]")
    turns = transcript_turns(events, frozenset(), redact)

    first = turns[0][1]
    assert first.startswith("What went wrong here?\n\n<historical_context>")
    assert "page: /utilities/index-photos; job_id: job-7; error: Could not read [media folder m1]/&lt;a&gt;.jpg" in first
    assert turns[2] == ("user", "Can you retry them?")


def test_the_latest_messages_context_is_not_repeated_in_history():
    from yaffo.site_agents.assistant.history import transcript_turns
    events = _events((0, "user", "Why did this fail?", {"context": {"job_id": "job-7"}}))
    assert transcript_turns(events, frozenset(), str) == [("user", "Why did this fail?")]


def test_plan_decisions_are_replayed_as_app_recorded_data():
    from yaffo.site_agents.assistant.history import transcript_turns
    events = _events(
        (0, "user", "Tag the trip", None),
        (1, "assistant", "Here's a plan; approve it on the card.", None),
        (2, "plan", "The user approved plan #3; step 1 failed: /Users/alex/<x>.jpg", {"plan_id": 3, "status": "PARTIAL"}),
        (3, "user", "Did it work?", None),
    )
    redact = lambda text: text.replace("/Users/alex", "~")
    turns = transcript_turns(events, frozenset(), redact)

    assert [role for role, _ in turns] == ["user", "assistant", "user"]
    assert turns[1][1].endswith(
        '<plan_update plan="3" status="PARTIAL">The user approved plan #3; step 1 failed: ~/&lt;x&gt;.jpg</plan_update>')


def test_changes_block_needs_scripts_and_lists_only_enabled_changes():
    actions = frozenset({"tag_media_items", "create_album"})
    prompt = build_assistant_system_prompt(frozenset({"library"}), actions)
    assert "<changes>" in prompt and "<plan_update>" in prompt
    assert "tag_media_items(tags)" in prompt and "create_album(" in prompt
    assert "delete_media_items" not in prompt and "untag_media_items" not in prompt
    assert "$ref" in prompt
    assert "can't change anything in the app or the library" not in prompt

    assert "<changes>" not in build_assistant_system_prompt(frozenset({"logs"}), actions)
    read_only = build_assistant_system_prompt(frozenset({"library"}), frozenset())
    assert "<changes>" not in read_only and "Scripts can only read" in read_only
