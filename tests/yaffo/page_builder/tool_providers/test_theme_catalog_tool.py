"""Unit tests for the theme catalog tools (theme_catalog_tool.py): the read-only
list_themes / get_theme the agent uses to adapt an existing palette. Exercised against
a throwaway database; built-in themes are read from their shipped static files.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo import themes
from yaffo.db import db
from yaffo.db.models import PAGE_VERSION_STATUS_ACCEPTED
from yaffo.page_builder.tool_providers import theme_catalog_tool as tct
from yaffo.themes import CustomTheme, ThemeAssets

pytestmark = pytest.mark.unit

SLUG = "vapor-wave"
SELECTOR = f'[data-theme="{SLUG}"]'


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


@pytest.fixture
def custom_theme(session):
    themes.save_custom_theme(
        CustomTheme(
            slug=SLUG,
            label="Vapor Wave",
            status=PAGE_VERSION_STATUS_ACCEPTED,
            conversations=[],
            published_theme=ThemeAssets(
                tokens_css=f"{SELECTOR} {{ --color-accent: #ff00aa; }}",
                skin_css=f"{SELECTOR} .navbar {{ background: var(--color-accent); }}",
            ),
            working_theme=None,
        ),
        session,
    )
    return SLUG


@pytest.fixture
def provider(session):
    return tct.ThemeCatalogToolProvider(session=session)


def test_advertises_list_and_get(provider):
    names = {t.name for t in provider.get_tools()}
    assert names == {"list_themes", "get_theme"}


def test_list_includes_builtins_and_custom(provider, custom_theme):
    listing = provider.call_tool("list_themes", {})
    assert "memphis: Memphis (built-in)" in listing
    assert f"{SLUG}: Vapor Wave (custom)" in listing


def test_get_builtin_returns_its_tokens(provider):
    result = provider.call_tool("get_theme", {"slug": "memphis"})
    assert '[data-theme="memphis"]' in result
    assert "tokens_css:" in result


def test_get_classic_is_baseline_note(provider):
    result = provider.call_tool("get_theme", {"slug": "classic"})
    assert "baseline" in result.lower()


def test_get_custom_returns_tokens_and_skin(provider, custom_theme):
    result = provider.call_tool("get_theme", {"slug": SLUG})
    assert "--color-accent: #ff00aa;" in result
    assert "skin_css:" in result
    assert ".navbar" in result


def test_get_unknown_slug(provider):
    result = provider.call_tool("get_theme", {"slug": "nope"})
    assert "No theme named" in result


def test_get_requires_slug(provider):
    assert "requires a slug" in provider.call_tool("get_theme", {})


def test_unknown_tool_name(provider):
    assert "Unknown tool" in provider.call_tool("nope", {})