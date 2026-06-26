"""The response-language contract shared by every generation agent.

The rule (reply in the user's language, fall back to the application locale) is
constant text in the cached system prompt; the selected locale is volatile and
rides in the user turn. These tests pin both halves across all three agents (page,
theme, automation), including the non-English and ambiguous-message cases.
"""
import pytest

from yaffo.site_agents.prompt_generator.automation_system_prompt import (
    build_automation_builder_system_prompt,
)
from yaffo.site_agents.prompt_generator.automation_user_prompt import (
    build_automation_user_message,
)
from yaffo.site_agents.prompt_generator.system_prompt import build_system_prompt
from yaffo.site_agents.prompt_generator.theme_system_prompt import (
    build_template_builder_system_prompt,
)
from yaffo.site_agents.prompt_generator.theme_user_prompt import build_theme_user_message
from yaffo.site_agents.prompt_generator.user_prompt import build_user_message

pytestmark = pytest.mark.unit

SYSTEM_PROMPTS = (
    build_system_prompt,
    build_template_builder_system_prompt,
    build_automation_builder_system_prompt,
)


def _user_turns(request, *, locale):
    """The same request rendered through each agent's user-turn builder, so one test
    can assert the locale contract holds across all three."""
    return (
        build_user_message(request, locale=locale),
        build_theme_user_message(request, slug="t", locale=locale),
        build_automation_user_message(request, slug="a", locale=locale),
    )


@pytest.mark.parametrize("build_prompt", SYSTEM_PROMPTS)
def test_system_prompt_states_the_response_language_rule(build_prompt):
    prompt = build_prompt()
    assert "<response_language>" in prompt
    # The fallback points at the locale carried in the user turn, not a baked value.
    assert "<application_locale>" in prompt


@pytest.mark.parametrize("build_prompt", SYSTEM_PROMPTS)
def test_system_prompt_pins_no_concrete_locale(build_prompt):
    # The rule is constant so it stays inside the cached prefix; the locale lives in
    # the (volatile) user turn. A leaked locale code here would invalidate the cache.
    prompt = build_prompt()
    assert "<application_locale>de" not in prompt
    assert "<application_locale>en" not in prompt


def test_user_turn_carries_the_selected_locale():
    for turn in _user_turns("make a gallery", locale="de"):
        assert "<application_locale>de</application_locale>" in turn


def test_user_turn_carries_a_non_english_request_verbatim():
    # The model is told (by the rule) to answer in the request's language; the request
    # itself must reach it unaltered for that to work.
    request = "Erstelle eine Galerie meiner Strandfotos"
    for turn in _user_turns(request, locale="en"):
        assert request in turn


def test_ambiguous_request_still_carries_the_locale_fallback():
    # An empty/ambiguous message is where the rule defers to <application_locale>, so
    # the fallback must be present even when there's nothing to infer a language from.
    for turn in _user_turns("   ", locale="de"):
        assert "<application_locale>de</application_locale>" in turn


def test_user_turn_defaults_to_english():
    for turn in _user_turns("hello", locale="en"):
        assert "<application_locale>en</application_locale>" in turn
    # And the same when the caller omits locale entirely.
    assert "<application_locale>en</application_locale>" in build_user_message("hi")
