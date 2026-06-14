"""Unit tests for the theme-builder user turn (build_theme_user_message): carries the
request, the theme's slug/selector, and the current CSS so a follow-up revises rather
than restarts.
"""
import pytest

from yaffo.page_builder.prompt_generator.theme_user_prompt import build_theme_user_message

pytestmark = pytest.mark.unit


def test_includes_request_and_selector():
    msg = build_theme_user_message("make it neon", slug="vapor-wave", label="Vapor Wave")
    assert "make it neon" in msg
    assert "<slug>vapor-wave</slug>" in msg
    assert '[data-theme="vapor-wave"]' in msg
    assert "Vapor Wave" in msg


def test_omits_current_theme_when_empty():
    # A brand-new theme has no CSS yet -> no current_theme section to revise.
    msg = build_theme_user_message("design something calm", slug="calm")
    assert "<current_theme>" not in msg


def test_includes_current_css_for_followups():
    msg = build_theme_user_message(
        "warmer please",
        slug="vapor-wave",
        tokens_css='[data-theme="vapor-wave"] { --color-accent: #ff00aa; }',
        skin_css='[data-theme="vapor-wave"] .navbar { background: var(--color-accent); }',
    )
    assert "<current_theme>" in msg
    assert "--color-accent: #ff00aa;" in msg
    assert "<skin_css>" in msg
    assert ".navbar" in msg


def test_skin_only_present_when_supplied():
    msg = build_theme_user_message(
        "tweak",
        slug="x",
        tokens_css='[data-theme="x"] { --color-bg: #000; }',
    )
    assert "<tokens_css>" in msg
    assert "<skin_css>" not in msg