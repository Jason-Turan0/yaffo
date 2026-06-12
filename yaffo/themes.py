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


# --- TEMPORARY STUB ----------------------------------------------------------
# A sample custom theme served as if an agent had stored it in
# ApplicationSettings, so the runtime-theme pipeline (settings picker, dynamic
# /themes/<slug>/*.css routes, widget-frame tokens, favicon route, and the
# empty-placeholder fallback) can be exercised end to end before the generator
# exists. A real DB row with the same slug overrides it. Remove once the
# theme-generator agent lands.
_STUB_THEMES: dict[str, CustomTheme] = {
    "sunset": CustomTheme(
        slug="sunset",
        label="Sunset (sample)",
        tokens_css="""\
[data-theme="sunset"] {
    /* Surfaces — warm peach cream */
    --color-bg: #fff4ec;
    --color-surface: #fffdfb;
    --color-surface-hover: #ffeede;
    --color-surface-sunken: #f7e2d3;

    /* Text — dusk plum */
    --color-text: #43273e;
    --color-text-secondary: #5d4057;
    --color-text-muted: #8a6f84;
    --color-text-faint: #c4afc0;

    --color-surface-inverse: #43273e;
    --color-text-inverse: #fff4ec;

    /* Borders — toasted peach */
    --color-border-subtle: #f9e8dc;
    --color-border: #efd6c4;
    --color-border-strong: #ddb89f;
    --color-border-hover: #c08a6b;

    /* Accent — sunset orange */
    --color-accent: #f4623a;
    --color-accent-hover: #d94e2a;
    --color-accent-soft: #ffe3d6;
    --color-accent-soft-text: #9c3517;
    --color-on-accent: #fff8f2;

    /* Status */
    --color-success: #4f9d69;
    --color-success-hover: #428a59;
    --color-success-soft: #def0e2;
    --color-success-soft-text: #1f5c35;
    --color-success-soft-border: #bfe3c8;
    --color-danger: #d2385a;
    --color-danger-hover: #b82c4c;
    --color-danger-soft: #fadbe2;
    --color-danger-soft-text: #861f3a;
    --color-danger-soft-border: #f2bcc9;
    --color-warning: #f5a623;
    --color-warning-soft: #fdeccd;
    --color-warning-soft-text: #7a5200;
    --color-warning-soft-border: #f3d9a4;
    --color-info: #5b8fc9;

    --color-neutral: #8a6f84;
    --color-neutral-hover: #6f5569;

    --color-disabled-bg: #f3e4da;
    --color-disabled-text: #a8918c;

    /* Shape */
    --radius-sm: 6px;
    --radius-md: 12px;

    /* Elevation — soft plum-tinted shadows */
    --color-backdrop: rgba(67, 39, 62, 0.5);
    --shadow-navbar: 0 2px 6px rgba(67, 39, 62, 0.08);
    --shadow-sm: 0 1px 3px rgba(67, 39, 62, 0.12);
    --shadow-md: 0 3px 10px rgba(67, 39, 62, 0.14);
    --shadow-lg: 0 6px 18px rgba(67, 39, 62, 0.18);
    --shadow-xl: 0 10px 32px rgba(67, 39, 62, 0.25);
}
""",
        skin_css="""\
[data-theme="sunset"] .navbar {
    background: linear-gradient(90deg, var(--color-warning-soft), var(--color-accent-soft));
}

/* Brand carries the theme mark so tab icon and logo match (app convention);
   custom themes have no static favicon file, so reference the favicon route. */
[data-theme="sunset"] .navbar-brand {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    color: var(--color-accent);
    font-weight: 800;
}

[data-theme="sunset"] .navbar-brand::before {
    content: "";
    width: 24px;
    height: 24px;
    flex: none;
    background: url("/favicon.ico?theme=sunset") center / contain no-repeat;
}

[data-theme="sunset"] .page-header h1 {
    display: inline-block;
    border-bottom: 4px solid var(--color-accent);
    padding-bottom: 4px;
}
""",
        favicon_svg="""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="sun" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#f5a623"/>
      <stop offset="1" stop-color="#f4623a"/>
    </linearGradient>
  </defs>
  <rect x="1" y="1" width="62" height="62" rx="14" fill="#fffdfb" stroke="#efd6c4" stroke-width="2"/>
  <circle cx="32" cy="28" r="14" fill="url(#sun)"/>
  <line x1="16" y1="30" x2="48" y2="30" stroke="#fffdfb" stroke-width="3"/>
  <line x1="20" y1="36" x2="44" y2="36" stroke="#fffdfb" stroke-width="3"/>
  <rect x="12" y="46" width="40" height="4" rx="2" fill="url(#sun)"/>
</svg>
""",
        placeholder_svg="""\
<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300" viewBox="0 0 400 300">
  <defs>
    <linearGradient id="sun" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#f5a623"/>
      <stop offset="1" stop-color="#f4623a"/>
    </linearGradient>
  </defs>
  <rect width="400" height="300" fill="#fff4ec"/>
  <rect x="120" y="60" width="160" height="120" rx="12" fill="#fffdfb" stroke="#efd6c4" stroke-width="2"/>
  <circle cx="200" cy="116" r="26" fill="url(#sun)"/>
  <line x1="170" y1="120" x2="230" y2="120" stroke="#fffdfb" stroke-width="5"/>
  <line x1="176" y1="131" x2="224" y2="131" stroke="#fffdfb" stroke-width="5"/>
  <rect x="160" y="150" width="80" height="6" rx="3" fill="url(#sun)"/>
  <text x="200" y="216" text-anchor="middle" font-family="-apple-system, 'Segoe UI', Roboto, sans-serif" font-size="15" fill="#8a6f84">image not found</text>
</svg>
""",
    ),
}


def get_custom_theme(slug: str) -> CustomTheme | None:
    row = (
        db.session.query(ApplicationSettings)
        .filter_by(name=_setting_name(slug))
        .first()
    )
    if row is not None:
        return CustomTheme(**json.loads(row.value))
    return _STUB_THEMES.get(slug)


def list_custom_themes() -> list[CustomTheme]:
    themes_by_slug = dict(_STUB_THEMES)
    rows = (
        db.session.query(ApplicationSettings)
        .filter(ApplicationSettings.name.like(f"{CUSTOM_THEME_PREFIX}%"))
        .order_by(ApplicationSettings.name)
        .all()
    )
    for row in rows:  # stored themes override stubs on slug collisions
        theme = CustomTheme(**json.loads(row.value))
        themes_by_slug[theme.slug] = theme
    return list(themes_by_slug.values())


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