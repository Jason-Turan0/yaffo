"""Theme registry and persistence.

Themes come in two tiers:

- **Built-in**: the styling contract is static/tokens.css (:root = the default
  "classic" look); each theme ships a token-override block
  (static/themes/<slug>/tokens.css), a skin file (static/themes/<slug>.css),
  and assets (static/themes/<slug>/favicon.svg + placeholder.svg). Registered
  in THEMES below; tests/yaffo/test_design_tokens.py keeps THEMES and the
  token files in sync.
- **Custom**: created at runtime (e.g. by an AI agent) and stored whole in the
  ApplicationSettings table, one row per theme (name "custom_theme:<slug>",
  value = CustomTheme as JSON). Their CSS is served by the /themes/<slug>/*.css
  routes (routes/base.py); base.html links only the active theme's stylesheet,
  so a new custom theme needs no template, registry, or static-file changes.

This module owns which themes exist and which one is active; base.html stamps
the active slug onto <html data-theme="…">.
"""
import json
import re
from dataclasses import asdict, dataclass

from yaffo.db import db
from yaffo.db.models import ApplicationSettings

THEME_SETTING_NAME = "theme"
DEFAULT_THEME = "classic"
CUSTOM_THEME_PREFIX = "custom_theme:"

# Custom slugs end up inside CSS selectors and URLs, so keep them strict.
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")

# slug -> display label; every non-default slug must ship its
# [data-theme="slug"] block in static/themes/<slug>/tokens.css (enforced by
# tests/yaffo/test_design_tokens.py).
THEMES: dict[str, str] = {
    "classic": "Classic",
    "darkroom": "Darkroom",
    "neobrutalist": "Neo-Brutalist",
    "scrapbook": "Scrapbook",
    "photos-app": "Photos App",
    "memphis": "Memphis",
}


@dataclass
class CustomTheme:
    """A runtime-defined theme, stored whole in ApplicationSettings.

    `tokens_css` must be a `[data-theme="<slug>"] { --… }` token-override block —
    it is the only part widget frames load, so it must stand alone. `skin_css`
    holds optional structural rules (scoped to the same attribute) that only the
    main app pages load. `favicon_svg`/`placeholder_svg` are optional; the
    default theme's files are served when they are empty."""
    slug: str
    label: str
    tokens_css: str
    skin_css: str = ""
    favicon_svg: str = ""
    placeholder_svg: str = ""


def _setting_name(slug: str) -> str:
    return f"{CUSTOM_THEME_PREFIX}{slug}"


def is_builtin(slug: str) -> bool:
    return slug in THEMES


def get_custom_theme(slug: str) -> CustomTheme | None:
    row = (
        db.session.query(ApplicationSettings)
        .filter_by(name=_setting_name(slug))
        .first()
    )
    if row is None:
        return None
    return CustomTheme(**json.loads(row.value))


def list_custom_themes() -> list[CustomTheme]:
    rows = (
        db.session.query(ApplicationSettings)
        .filter(ApplicationSettings.name.like(f"{CUSTOM_THEME_PREFIX}%"))
        .order_by(ApplicationSettings.name)
        .all()
    )
    return [CustomTheme(**json.loads(row.value)) for row in rows]


def list_themes() -> dict[str, str]:
    """slug -> label for every selectable theme, built-in then custom."""
    merged = dict(THEMES)
    merged.update({theme.slug: theme.label for theme in list_custom_themes()})
    return merged


def theme_exists(slug: str) -> bool:
    return is_builtin(slug) or get_custom_theme(slug) is not None


def save_custom_theme(theme: CustomTheme) -> None:
    """Create or update a custom theme (validates before touching the DB)."""
    if not _SLUG_RE.match(theme.slug):
        raise ValueError(
            f"Invalid theme slug {theme.slug!r}: lowercase letters, digits, and "
            "hyphens, starting with a letter"
        )
    if is_builtin(theme.slug):
        raise ValueError(f"Slug {theme.slug!r} belongs to a built-in theme")
    if not (theme.label or "").strip():
        raise ValueError("Theme label is required")
    if f'[data-theme="{theme.slug}"]' not in theme.tokens_css:
        raise ValueError(
            f'tokens_css must contain a [data-theme="{theme.slug}"] override block'
        )

    payload = json.dumps(asdict(theme))
    row = (
        db.session.query(ApplicationSettings)
        .filter_by(name=_setting_name(theme.slug))
        .first()
    )
    if row:
        row.value = payload
    else:
        db.session.add(
            ApplicationSettings(name=_setting_name(theme.slug), type="json", value=payload)
        )
    db.session.commit()


def delete_custom_theme(slug: str) -> None:
    """Remove a custom theme; if it was active, fall back to the default."""
    row = (
        db.session.query(ApplicationSettings)
        .filter_by(name=_setting_name(slug))
        .first()
    )
    if row is None:
        return
    db.session.delete(row)
    db.session.commit()
    if get_theme() == slug:
        set_theme(DEFAULT_THEME)


_cached_theme: str | None = None

def get_theme() -> str:
    global _cached_theme
    if _cached_theme is not None:
        return _cached_theme

    setting = (
        db.session.query(ApplicationSettings)
        .filter_by(name=THEME_SETTING_NAME)
        .first()
    )
    if setting and theme_exists(setting.value):
        _cached_theme = setting.value
    else:
        _cached_theme = DEFAULT_THEME
    return _cached_theme

def set_theme(theme: str) -> None:
    global _cached_theme
    if not theme_exists(theme):
        raise ValueError(f"Unknown theme: {theme!r}")
    setting = (
        db.session.query(ApplicationSettings)
        .filter_by(name=THEME_SETTING_NAME)
        .first()
    )
    if setting:
        setting.value = theme
    else:
        db.session.add(
            ApplicationSettings(name=THEME_SETTING_NAME, type="str", value=theme)
        )
    db.session.commit()
    _cached_theme = theme