"""Unit tests for the theme tool (yaffo/page_builder/tool_providers/theme_tool.py).

The tool persists directly into a custom theme's working_theme, so these exercise it
against a throwaway database: the model-facing schema, the sanitation rules (scoped
override block, token-driven skin, inert/self-contained CSS), the partial-update merge,
and the not-found guard.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo import themes
from yaffo.db import db
from yaffo.db.models import PAGE_VERSION_STATUS_ACCEPTED
from yaffo.page_builder.tool_providers import theme_tool as tt
from yaffo.page_builder.tool_providers.tool_provider_types import ToolResult
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
def theme(session):
    themes.save_custom_theme(
        CustomTheme(
            slug=SLUG,
            label="Vapor Wave",
            status=PAGE_VERSION_STATUS_ACCEPTED,
            conversations=[],
            published_theme=ThemeAssets(tokens_css=f"{SELECTOR} {{\n}}\n"),
            working_theme=None,
        ),
        session,
    )
    return SLUG


@pytest.fixture
def provider(session, theme):
    return tt.ThemeToolProvider(theme, session=session)


def _tokens(body="--color-accent: #ff00aa;"):
    return f"{SELECTOR} {{\n  {body}\n}}"


def _working(session):
    return themes.get_custom_theme(SLUG, session).working_theme


# --- schema --------------------------------------------------------------------

def test_advertises_write_theme_with_required_tokens(provider):
    [tool] = provider.get_tools()
    assert tool.name == "write_theme"
    assert tool.input_schema["required"] == ["tokens_css"]
    assert set(tool.input_schema["properties"]) == {
        "tokens_css", "skin_css", "favicon_svg", "placeholder_svg",
    }
    # the slug-scoped selector is surfaced to the model on the tool itself.
    assert SELECTOR in tool.description


# --- happy path ----------------------------------------------------------------

def test_write_persists_working_theme(provider, session):
    result = provider.call_tool("write_theme", {"tokens_css": _tokens()})
    assert isinstance(result, ToolResult)
    assert result.host_data["slug"] == SLUG

    working = _working(session)
    assert working is not None
    assert "--color-accent: #ff00aa;" in working.tokens_css


def test_write_leaves_published_theme_untouched(provider, session):
    provider.call_tool("write_theme", {"tokens_css": _tokens()})
    published = themes.get_custom_theme(SLUG, session).published_theme
    assert published.tokens_css == f"{SELECTOR} {{\n}}\n"  # the empty starting block


def test_write_accepts_scoped_token_driven_skin(provider, session):
    skin = f"{SELECTOR} .navbar {{ background: var(--color-accent); }}"
    result = provider.call_tool("write_theme", {"tokens_css": _tokens(), "skin_css": skin})
    assert isinstance(result, ToolResult)
    assert _working(session).skin_css == skin


def test_refining_call_keeps_prior_skin(provider, session):
    skin = f"{SELECTOR} .navbar {{ background: var(--color-accent); }}"
    provider.call_tool("write_theme", {"tokens_css": _tokens(), "skin_css": skin})
    # a follow-up that only changes tokens must not drop the earlier skin.
    provider.call_tool("write_theme", {"tokens_css": _tokens("--color-accent: #00ffcc;")})

    working = _working(session)
    assert "#00ffcc" in working.tokens_css
    assert working.skin_css == skin


# --- sanitation ----------------------------------------------------------------

def test_unscoped_tokens_block_is_rejected(provider, session):
    result = provider.call_tool("write_theme", {"tokens_css": ":root { --color-accent: #ff00aa; }"})
    assert isinstance(result, str)
    assert SELECTOR in result  # tells the model the selector it must use
    assert _working(session) is None  # nothing saved


def test_skin_with_raw_color_is_rejected(provider, session):
    skin = f"{SELECTOR} .navbar {{ background: #ff00aa; }}"
    result = provider.call_tool("write_theme", {"tokens_css": _tokens(), "skin_css": skin})
    assert isinstance(result, str)
    assert "var(--token)" in result
    assert _working(session) is None


def test_unscoped_skin_is_rejected(provider, session):
    skin = ".navbar { background: var(--color-accent); }"
    result = provider.call_tool("write_theme", {"tokens_css": _tokens(), "skin_css": skin})
    assert isinstance(result, str)
    assert _working(session) is None


def test_external_url_is_rejected(provider, session):
    tokens = _tokens("--font-body: url(https://evil.test/f.woff);")
    result = provider.call_tool("write_theme", {"tokens_css": tokens})
    assert isinstance(result, str)
    assert "external" in result.lower()
    assert _working(session) is None


def test_script_injection_is_rejected(provider, session):
    skin = f"{SELECTOR} x {{}} </style><script>alert(1)</script>"
    result = provider.call_tool("write_theme", {"tokens_css": _tokens(), "skin_css": skin})
    assert isinstance(result, str)
    assert _working(session) is None


def test_import_at_rule_is_rejected(provider, session):
    tokens = f'@import "x.css"; {_tokens()}'
    result = provider.call_tool("write_theme", {"tokens_css": tokens})
    assert isinstance(result, str)
    assert _working(session) is None


# --- guards --------------------------------------------------------------------

def test_missing_theme_reports_not_found(session):
    provider = tt.ThemeToolProvider("ghost", session=session)
    result = provider.call_tool("write_theme", {"tokens_css": '[data-theme="ghost"] { --color-bg: #fff; }'})
    assert isinstance(result, str)
    assert "not found" in result.lower()


def test_unknown_tool_name(provider):
    assert "Unknown tool" in provider.call_tool("nope", {})