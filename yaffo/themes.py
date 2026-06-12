"""Theme registry and persistence.

The styling contract lives in static/tokens.css: the :root block is the
default ("classic") look, and a theme is a [data-theme="…"] block that
overrides those token values. This module owns which themes exist and which
one is active; base.html stamps the active slug onto <html data-theme="…">.
"""
from yaffo.db import db
from yaffo.db.models import ApplicationSettings

THEME_SETTING_NAME = "theme"
DEFAULT_THEME = "classic"

# slug -> display label; every non-default slug must have a matching
# [data-theme="slug"] block in static/tokens.css (enforced by
# tests/yaffo/test_design_tokens.py).
THEMES: dict[str, str] = {
    "classic": "Classic",
    "neobrutalist": "Neo-Brutalist",
    "scrapbook": "Scrapbook",
}

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
    if setting and setting.value in THEMES:
        _cached_theme = setting.value
    else:
        _cached_theme = DEFAULT_THEME
    return _cached_theme

def set_theme(theme: str) -> None:
    global _cached_theme
    if theme not in THEMES:
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