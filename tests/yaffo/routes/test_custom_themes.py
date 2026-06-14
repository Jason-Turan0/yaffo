"""Custom (runtime) themes: stored whole in ApplicationSettings, served by the
dynamic /themes/<slug>/*.css routes, and selectable alongside built-ins."""
import pytest

from yaffo import themes
from yaffo.db.models import PAGE_VERSION_STATUS_ACCEPTED, PAGE_VERSION_STATUS_READY
from yaffo.themes import CustomTheme, ThemeAssets

TOKENS = '[data-theme="vaporwave"] { --color-accent: #ff71ce; --color-bg: #05ffa1; }'
SKIN = '[data-theme="vaporwave"] .navbar-brand { letter-spacing: 0.3em; }'
FAVICON = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="8"/></svg>'


def _vaporwave(
    *,
    slug="vaporwave",
    label="Vaporwave",
    tokens_css=TOKENS,
    skin_css=SKIN,
    favicon_svg=FAVICON,
    placeholder_svg="",
    status=PAGE_VERSION_STATUS_ACCEPTED,
) -> CustomTheme:
    # The published draft is what the app serves; flat kwargs map onto its assets so
    # the existing cases (overriding tokens / favicon / placeholder) read naturally.
    return CustomTheme(
        slug=slug,
        label=label,
        status=status,
        conversations=[],
        published_theme=ThemeAssets(
            tokens_css=tokens_css,
            skin_css=skin_css,
            favicon_svg=favicon_svg,
            placeholder_svg=placeholder_svg,
        ),
        working_theme=None,
    )


# ---- registry / persistence -------------------------------------------------

def test_save_get_list_roundtrip(app):
    themes.save_custom_theme(_vaporwave())
    assert themes.get_custom_theme("vaporwave") == _vaporwave()
    assert themes.list_themes()["vaporwave"] == "Vaporwave"
    assert themes.theme_exists("vaporwave")

    themes.save_custom_theme(_vaporwave(label="Vapor Wave 2"))
    assert themes.get_custom_theme("vaporwave").label == "Vapor Wave 2"
    slugs = [theme.slug for theme in themes.list_custom_themes()]
    assert slugs.count("vaporwave") == 1  # updated in place, not duplicated


def _with_working_draft(slug="vaporwave", tokens='[data-theme="vaporwave"] { --color-bg: #000; }'):
    """Save the vaporwave theme carrying a finished working draft (status READY)."""
    themes.save_custom_theme(_vaporwave(status=PAGE_VERSION_STATUS_READY))
    theme = themes.get_custom_theme(slug)
    theme.working_theme = ThemeAssets(tokens_css=tokens)
    themes.save_custom_theme(theme)
    return slug


def test_publish_theme_promotes_working_to_published(app):
    slug = _with_working_draft()
    themes.publish_theme(slug)

    theme = themes.get_custom_theme(slug)
    assert theme.status == PAGE_VERSION_STATUS_ACCEPTED
    assert theme.working_theme is None
    assert "--color-bg: #000;" in theme.published_theme.tokens_css


def test_publish_theme_without_draft_raises(app):
    themes.save_custom_theme(_vaporwave())  # ACCEPTED, no working draft
    with pytest.raises(ValueError, match="no working draft"):
        themes.publish_theme("vaporwave")


def test_discard_theme_draft_reverts_to_published(app):
    slug = _with_working_draft()
    published_before = themes.get_custom_theme(slug).published_theme.tokens_css

    themes.discard_theme_draft(slug)
    theme = themes.get_custom_theme(slug)
    assert theme.status == PAGE_VERSION_STATUS_ACCEPTED
    assert theme.working_theme is None
    assert theme.published_theme.tokens_css == published_before


def test_builtins_unaffected_by_custom_registry(app):
    themes.save_custom_theme(_vaporwave())
    merged = themes.list_themes()
    for slug, label in themes.THEMES.items():
        assert merged[slug] == label
    assert not themes.is_builtin("vaporwave")


@pytest.mark.parametrize(
    "bad_theme, match",
    [
        (_vaporwave(slug="classic"), "built-in"),
        (_vaporwave(slug="Bad Slug!"), "Invalid theme slug"),
        (_vaporwave(label="  "), "label"),
        (_vaporwave(tokens_css=":root { --color-accent: red; }"), "data-theme"),
    ],
)
def test_save_rejects_invalid_themes(app, bad_theme, match):
    with pytest.raises(ValueError, match=match):
        themes.save_custom_theme(bad_theme)
    assert themes.get_custom_theme(bad_theme.slug) is None


def test_delete_active_custom_theme_falls_back_to_default(app):
    themes.save_custom_theme(_vaporwave())
    themes.set_theme("vaporwave")
    themes.delete_custom_theme("vaporwave")
    assert themes.get_custom_theme("vaporwave") is None
    assert themes.get_theme() == themes.DEFAULT_THEME


# ---- selection + rendering --------------------------------------------------

def test_custom_theme_selectable_and_stamped_on_html(app, client):
    themes.save_custom_theme(_vaporwave())
    response = client.post("/themes/vaporwave/default")
    assert response.status_code == 204

    page = client.get("/settings")
    assert b'data-theme="vaporwave"' in page.data
    assert b'/themes/vaporwave/theme.css' in page.data


def test_themes_page_lists_custom_theme(app, client):
    themes.save_custom_theme(_vaporwave())
    page = client.get("/themes/vaporwave").data.decode()
    assert '/themes/vaporwave"' in page
    assert "Vaporwave" in page


# ---- dynamic css routes -----------------------------------------------------

def test_theme_css_serves_builtin_skin_file(client):
    response = client.get("/themes/classic/theme.css")
    assert response.status_code == 200
    assert response.mimetype == "text/css"
    assert b'[data-theme="classic"]' in response.data


def test_theme_css_serves_custom_tokens_and_skin(app, client):
    themes.save_custom_theme(_vaporwave())
    response = client.get("/themes/vaporwave/theme.css")
    assert response.status_code == 200
    assert response.mimetype == "text/css"
    assert TOKENS in response.text
    assert SKIN in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_preview_css_serves_working_draft_when_present(app, client):
    _with_working_draft()  # vaporwave with a #000 bg draft pending
    response = client.get("/themes/vaporwave/preview.css")
    assert response.status_code == 200
    assert "--color-bg: #000;" in response.text  # the draft, not the published tokens
    assert TOKENS not in response.text


def test_preview_css_falls_back_to_published_without_draft(app, client):
    themes.save_custom_theme(_vaporwave())  # no working draft
    response = client.get("/themes/vaporwave/preview.css")
    assert response.status_code == 200
    assert TOKENS in response.text


def test_preview_css_serves_builtin(client):
    response = client.get("/themes/neobrutalist/preview.css")
    assert response.status_code == 200
    assert '[data-theme="neobrutalist"]' in response.text


def test_preview_css_unknown_slug_404s(client):
    assert client.get("/themes/nope/preview.css").status_code == 404


def test_themes_page_renders_as_the_viewed_theme(app, client):
    themes.save_custom_theme(_vaporwave())  # not the active default
    page = client.get("/themes/vaporwave").data.decode()
    assert 'data-theme="vaporwave"' in page  # the page previews the theme it's viewing
    assert "/themes/vaporwave/preview.css" in page  # via the preview stylesheet


def test_themes_page_preview_reflects_working_draft(app, client):
    _with_working_draft()
    css = client.get("/themes/vaporwave/preview.css").text
    assert "--color-bg: #000;" in css  # the page's preview sheet serves the draft


def test_theme_css_unknown_slug_404s(client):
    assert client.get("/themes/nope/theme.css").status_code == 404


def test_tokens_css_is_tokens_only_for_custom(app, client):
    themes.save_custom_theme(_vaporwave())
    response = client.get("/themes/vaporwave/tokens.css")
    assert TOKENS in response.text
    assert SKIN not in response.text


def test_tokens_css_serves_builtin_token_file(client):
    response = client.get("/themes/memphis/tokens.css")
    assert response.status_code == 200
    assert '[data-theme="memphis"]' in response.text
    assert ".navbar" not in response.text  # tokens only, no skin rules


def test_tokens_css_empty_for_default_theme(client):
    """Classic's tokens are tokens.css's :root block, which frames already link."""
    response = client.get("/themes/classic/tokens.css")
    assert response.status_code == 200
    assert "[data-theme" not in response.text


# ---- themed assets ----------------------------------------------------------

def test_favicon_served_from_db_for_custom_theme(app, client):
    themes.save_custom_theme(_vaporwave())
    response = client.get("/favicon.ico?theme=vaporwave")
    assert response.mimetype == "image/svg+xml"
    assert response.data.decode() == FAVICON


def test_favicon_falls_back_to_default_without_svg(app, client):
    themes.save_custom_theme(_vaporwave(favicon_svg=""))
    response = client.get("/favicon.ico?theme=vaporwave")
    assert response.status_code == 200
    assert b"<svg" in response.data  # classic's file


def test_placeholder_falls_back_to_default_without_svg(app, client):
    themes.save_custom_theme(_vaporwave(placeholder_svg=""))
    themes.set_theme("vaporwave")
    response = client.get("/placeholder")
    assert response.status_code == 200
    assert b"Image not found" in response.data  # classic's file


def test_placeholder_served_from_db_when_present(app, client):
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>no pic</text></svg>'
    themes.save_custom_theme(_vaporwave(placeholder_svg=svg))
    themes.set_theme("vaporwave")
    response = client.get("/placeholder")
    assert response.data.decode() == svg


# ---- widget frames ----------------------------------------------------------

def test_widget_frame_links_custom_tokens(app, client):
    """Widget iframes load the custom token block (and never the skin css)."""
    themes.save_custom_theme(_vaporwave())
    themes.set_theme("vaporwave")
    from yaffo.db import db
    from yaffo.db.repositories import custom_page_repository as page_repo

    page_id = page_repo.create_page(db.session, title="T", subtitle="").id
    page_repo.save_page_widgets(db.session, page_id, [
        {"id": "w1", "title": "W", "html": "<div></div>", "x": 0, "y": 0, "w": 4, "h": 3},
    ])
    response = client.get(f"/pages/{page_id}/widgets/w1/frame")
    assert response.status_code == 200
    assert b'data-theme="vaporwave"' in response.data
    assert b"/themes/vaporwave/tokens.css" in response.data