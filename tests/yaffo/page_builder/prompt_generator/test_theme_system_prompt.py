"""Unit tests for the theme-builder system prompt.

It must surface the real token contract (derived from static/tokens.css so it can't
drift) and the write_theme rules, while staying free of any per-theme (volatile) data
so the cached prefix holds across every theme.
"""
import pytest

from yaffo.page_builder.prompt_generator.theme_system_prompt import (
    _token_defaults,
    build_template_builder_system_prompt,
)

pytestmark = pytest.mark.unit


def test_lists_real_tokens_with_defaults():
    defaults = _token_defaults()
    joined = "\n".join(defaults)
    # a representative token from each major group, with its classic default value.
    assert "--color-accent: #007BFF" in joined
    assert "--color-bg: #f8f9fa" in joined
    assert any(line.startswith("--font-size-base:") for line in defaults)
    assert any(line.startswith("--radius-md:") for line in defaults)


def test_prompt_includes_tokens_and_contract():
    prompt = build_template_builder_system_prompt()
    assert "--color-accent: #007BFF" in prompt
    assert "write_theme" in prompt
    assert '[data-theme="<slug>"]' in prompt
    assert "skin_css" in prompt


def test_prompt_is_volatile_free():
    # No per-theme slug or other request data leaks into the cached prefix.
    prompt = build_template_builder_system_prompt()
    assert "[data-theme=\"custom" not in prompt
    assert "vapor-wave" not in prompt