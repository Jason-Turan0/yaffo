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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import ApplicationSettings, PAGE_VERSION_STATUS_ACCEPTED

# Built-in themes ship their CSS as static files (tokens.css override block +
# <slug>.css skin); custom themes carry theirs in the DB.
_THEMES_STATIC_DIR = Path(__file__).resolve().parent / "static" / "themes"

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
class ThemeAssets:
    tokens_css: str
    skin_css: str = ""
    favicon_svg: str = ""
    placeholder_svg: str = ""

@dataclass
class ThemeConversation:
    type: str
    content: str
    created_at: str

@dataclass
class CustomTheme:
    """A runtime-defined theme, stored whole in ApplicationSettings.
    `tokens_css` must be a `[data-theme="<slug>"] { --… }` token-override block —
    it is the only part widget frames load, so it must stand alone. `skin_css`
    holds optional structural rules (scoped to the same attribute) that only the
    main app pages load. `favicon_svg`/`placeholder_svg` are optional; the
    default theme's files are served when they are empty."""
    slug: str
    status: str
    label: str
    conversations: list[ThemeConversation]
    published_theme: Optional[ThemeAssets]
    working_theme: Optional[ThemeAssets]

def _setting_name(slug: str) -> str:
    return f"{CUSTOM_THEME_PREFIX}{slug}"


def _session(session: Optional[Session]) -> Session:
    """The session to read/write under. Routes pass nothing and use Flask-SQLAlchemy's
    request-scoped db.session; the background worker (no app context) passes its own
    SessionFactory session explicitly, the same way the page repository takes one."""
    return session if session is not None else db.session


def _theme_from_json(value: str) -> "CustomTheme":
    """Rebuild a CustomTheme from its stored JSON, rehydrating the nested
    dataclasses (asdict flattens them, so a bare ** would leave plain dicts)."""
    data = json.loads(value)
    data["conversations"] = [
        ThemeConversation(**c) for c in data.get("conversations") or []
    ]
    for field in ("published_theme", "working_theme"):
        if data.get(field) is not None:
            data[field] = ThemeAssets(**data[field])
    return CustomTheme(**data)


def is_builtin(slug: str) -> bool:
    return slug in THEMES


def get_custom_theme(slug: str, session: Optional[Session] = None) -> CustomTheme | None:
    row = (
        _session(session).query(ApplicationSettings)
        .filter_by(name=_setting_name(slug))
        .first()
    )
    if row is not None:
        return _theme_from_json(row.value)
    return None


def list_custom_themes(session: Optional[Session] = None) -> list[CustomTheme]:
    rows = (
        _session(session).query(ApplicationSettings)
        .filter(ApplicationSettings.name.like(f"{CUSTOM_THEME_PREFIX}%"))
        .order_by(ApplicationSettings.name)
        .all()
    )
    return [_theme_from_json(row.value) for row in rows]


def list_themes(session: Optional[Session] = None) -> dict[str, str]:
    """slug -> label for every selectable theme, built-in then custom."""
    merged = dict(THEMES)
    merged.update({theme.slug: theme.label for theme in list_custom_themes(session)})
    return merged


def read_theme_css(slug: str, session: Optional[Session] = None) -> Optional[ThemeAssets]:
    """The token + skin CSS a theme contributes, for any theme — built-in themes from
    their static files, custom themes from their published draft. Only the styling pair
    is returned (favicon/placeholder are left empty); icon art, when a theme has any,
    already lives in the skin. None if the slug is unknown. Built-in `classic` has no
    override file, so its tokens come back empty (they are static/tokens.css's :root
    contract)."""
    if is_builtin(slug):
        tokens = _THEMES_STATIC_DIR / slug / "tokens.css"
        skin = _THEMES_STATIC_DIR / f"{slug}.css"
        return ThemeAssets(
            tokens_css=tokens.read_text() if tokens.is_file() else "",
            skin_css=skin.read_text() if skin.is_file() else "",
        )
    custom = get_custom_theme(slug, session)
    return custom.published_theme if custom else None


def theme_exists(slug: str) -> bool:
    return is_builtin(slug) or get_custom_theme(slug) is not None


def save_custom_theme(theme: CustomTheme, session: Optional[Session] = None) -> None:
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
    if f'[data-theme="{theme.slug}"]' not in theme.published_theme.tokens_css:
        raise ValueError(
            f'tokens_css must contain a [data-theme="{theme.slug}"] override block'
        )

    sess = _session(session)
    payload = json.dumps(asdict(theme))
    row = (
        sess.query(ApplicationSettings)
        .filter_by(name=_setting_name(theme.slug))
        .first()
    )
    if row:
        row.value = payload
    else:
        sess.add(
            ApplicationSettings(name=_setting_name(theme.slug), type="json", value=payload)
        )
    sess.commit()


def add_theme_message(slug: str, entry_type: str, content: str, session: Optional[Session] = None) -> None:
    """Append a line (user / assistant / status / error) to a theme's transcript.
    Used by the chat route to record the prompt and by the generator task to stream
    its progress."""
    theme = get_custom_theme(slug, session)
    if theme is None:
        raise ValueError(f"Unknown custom theme: {slug!r}")
    theme.conversations.append(
        ThemeConversation(type=entry_type, content=content, created_at=datetime.now(timezone.utc).isoformat())
    )
    save_custom_theme(theme, session)


def set_theme_status(slug: str, status: str, session: Optional[Session] = None) -> None:
    """Move a theme to a new generation status (a PAGE_VERSION_STATUS_* value).
    The generator task polls this between iterations and stops when it is no longer
    IN_PROGRESS, so writing any terminal status here doubles as the cancel signal."""
    theme = get_custom_theme(slug, session)
    if theme is None:
        raise ValueError(f"Unknown custom theme: {slug!r}")
    theme.status = status
    save_custom_theme(theme, session)


def publish_theme(slug: str, session: Optional[Session] = None) -> None:
    """Promote a theme's working draft to its published, live form: the working_theme
    becomes the published_theme the app serves, the draft is cleared, and the theme
    returns to ACCEPTED (idle). Raises if there is no draft to publish."""
    theme = get_custom_theme(slug, session)
    if theme is None:
        raise ValueError(f"Unknown custom theme: {slug!r}")
    if theme.working_theme is None:
        raise ValueError(f"Theme {slug!r} has no working draft to publish")
    theme.published_theme = theme.working_theme
    theme.working_theme = None
    theme.status = PAGE_VERSION_STATUS_ACCEPTED
    save_custom_theme(theme, session)


def discard_theme_draft(slug: str, session: Optional[Session] = None) -> None:
    """Drop a theme's working draft and return it to ACCEPTED (idle). The published
    theme is untouched, so the live look is unchanged."""
    theme = get_custom_theme(slug, session)
    if theme is None:
        raise ValueError(f"Unknown custom theme: {slug!r}")
    theme.working_theme = None
    theme.status = PAGE_VERSION_STATUS_ACCEPTED
    save_custom_theme(theme, session)


def _rewrite_theme_slug(theme: CustomTheme, old_slug: str, new_slug: str) -> None:
    """Re-point the slug baked into a theme's CSS at a new slug: the
    `[data-theme="…"]` selectors every block is scoped by, and the `theme=…` query
    the skin uses to fetch the theme's own favicon."""
    replacements = (
        (f'[data-theme="{old_slug}"]', f'[data-theme="{new_slug}"]'),
        (f"theme={old_slug}", f"theme={new_slug}"),
    )
    for assets in (theme.published_theme, theme.working_theme):
        if assets is None:
            continue
        for old, new in replacements:
            assets.tokens_css = assets.tokens_css.replace(old, new)
            assets.skin_css = assets.skin_css.replace(old, new)


def rename_custom_theme(
    old_slug: str, new_slug: str, new_label: str, session: Optional[Session] = None
) -> None:
    """Rename a custom theme. The slug moves with the label, so the
    ApplicationSettings key, the `[data-theme]` selectors baked into the published
    and working CSS, and (when this theme is the active default) the default-theme
    setting all follow. `new_slug` is the caller-validated, collision-free target;
    pass `new_slug == old_slug` to change only the label."""
    if is_builtin(old_slug):
        raise ValueError(f"Slug {old_slug!r} belongs to a built-in theme")
    sess = _session(session)
    theme = get_custom_theme(old_slug, session)
    if theme is None:
        raise ValueError(f"Unknown custom theme: {old_slug!r}")

    was_default = get_theme() == old_slug
    theme.label = new_label
    if new_slug != old_slug:
        theme.slug = new_slug
        _rewrite_theme_slug(theme, old_slug, new_slug)
        old_row = (
            sess.query(ApplicationSettings)
            .filter_by(name=_setting_name(old_slug))
            .first()
        )
        if old_row is not None:
            sess.delete(old_row)

    save_custom_theme(theme, session)

    if new_slug != old_slug and was_default:
        set_theme(new_slug)


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