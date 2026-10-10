"""Earlier builder turns as the model gets them on a follow-up."""
import pytest

from yaffo.site_agents.common.history import NO_REPLY, builder_history

pytestmark = pytest.mark.unit


def test_earlier_turns_are_replayed_and_the_current_request_is_left_out():
    entries = [("user", "make a gallery"), ("status", "Creating widget…"),
               ("assistant", "Here is a gallery."), ("user", "add captions")]

    assert builder_history(entries) == [("user", "make a gallery"), ("assistant", "Here is a gallery.")]


def test_a_first_request_has_no_history():
    assert builder_history([("user", "make a gallery")]) == []


def test_several_replies_in_one_run_become_one_turn():
    entries = [("user", "a"), ("assistant", "first"), ("status", "Working…"), ("assistant", "second"),
               ("user", "b")]

    assert builder_history(entries) == [("user", "a"), ("assistant", "first\n\nsecond")]


def test_a_request_that_failed_still_alternates():
    entries = [("user", "a"), ("error", "Generation error: boom"), ("user", "b"), ("assistant", "done"),
               ("user", "c")]

    assert builder_history(entries) == [("user", "a"), ("assistant", NO_REPLY), ("user", "b"),
                                        ("assistant", "done")]


def test_leading_assistant_lines_are_dropped():
    assert builder_history([("assistant", "hello"), ("user", "a"), ("assistant", "ok"), ("user", "b")]) == [
        ("user", "a"), ("assistant", "ok")]


def test_the_oldest_turns_go_first_when_over_budget():
    entries = [("user", "old" * 10), ("assistant", "x" * 10), ("user", "new"), ("assistant", "y"),
               ("user", "current")]

    assert builder_history(entries, max_chars=10) == [("user", "new"), ("assistant", "y")]
