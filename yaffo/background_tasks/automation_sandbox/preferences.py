"""App preferences the assistant can change on request: the default theme, the
language, the distance unit, and the sidebar filter layout. Assistant profile
only. Each change is low risk: undo restores the value it replaced unless it was
changed again since (`expected`).

These pick among existing choices. Editing a theme's code stays with the theme
builder (ai-assistant.md → Never offered).
"""
from typing import Any, Optional

from sqlalchemy.orm import Session

from yaffo import themes
from yaffo.background_tasks.automation_sandbox.host_types import HostCall
from yaffo.distance_units import get_saved_distance_unit, normalize_distance_unit, set_distance_unit as save_unit
from yaffo.i18n import SUPPORTED_LOCALES, clear_locale, get_saved_locale, normalize_locale
from yaffo.i18n import set_locale as save_locale
from yaffo.routes.filter_config import FILTERS, load_layout, save_layout


def _skip(expected: Optional[dict], current: Any) -> bool:
    return expected is not None and expected.get("value") != current


# ---- theme ------------------------------------------------------------------------

def set_default_theme(session: Session, slug: str, expected: Optional[dict] = None) -> None:
    """Make an existing theme (built-in or custom) the app's theme."""
    if _skip(expected, themes.saved_theme(session)):
        return
    themes.set_theme(slug, session)


def theme_exists(args: list[Any], session: Session) -> str | None:
    slug = args[0] if args else None
    return None if isinstance(slug, str) and themes.theme_exists(slug, session) else f"No theme {slug!r}"


def summarize_set_default_theme(args: list[Any], session: Session) -> str:
    label = themes.list_themes(session).get(args[0]) if args else None
    return f"Switch the theme to '{label or (args[0] if args else '')}'"


def undo_set_default_theme(args: list[Any], session: Session) -> list[HostCall]:
    previous = themes.saved_theme(session)
    return [] if previous == args[0] else [HostCall("set_default_theme", [previous, {"value": args[0]}])]


# ---- language -----------------------------------------------------------------------

def set_locale(session: Session, locale: Optional[str], expected: Optional[dict] = None) -> None:
    """Set the app's language by code, or None to follow the browser's language."""
    if _skip(expected, get_saved_locale(session)):
        return
    if locale is None:
        clear_locale(session)
    elif not save_locale(locale, session):
        raise ValueError(f"Unsupported language {locale!r}; choose one of {', '.join(SUPPORTED_LOCALES)}")


def summarize_set_locale(args: list[Any], session: Session) -> str:
    code = normalize_locale(args[0]) if args and isinstance(args[0], str) else None
    return f"Switch the language to {SUPPORTED_LOCALES[code]}" if code else "Follow the browser's language"


def undo_set_locale(args: list[Any], session: Session) -> list[HostCall]:
    previous = get_saved_locale(session)
    new = normalize_locale(args[0]) if isinstance(args[0], str) else None
    return [] if previous == new else [HostCall("set_locale", [previous, {"value": new}])]


# ---- distance unit ---------------------------------------------------------------------

def set_distance_unit(session: Session, unit: str, expected: Optional[dict] = None) -> None:
    """Show distances in miles ("mi") or kilometers ("km")."""
    if _skip(expected, get_saved_distance_unit(session)):
        return
    if not save_unit(unit, session):
        raise ValueError('Unsupported distance unit; choose "mi" or "km"')


def summarize_set_distance_unit(args: list[Any], session: Session) -> str:
    unit = normalize_distance_unit(args[0]) if args and isinstance(args[0], str) else None
    return {"mi": "Show distances in miles", "km": "Show distances in kilometers"}.get(unit, "Change the distance unit")


def undo_set_distance_unit(args: list[Any], session: Session) -> list[HostCall]:
    previous = get_saved_distance_unit(session)
    new = normalize_distance_unit(args[0]) if isinstance(args[0], str) else None
    return [] if previous == new else [HostCall("set_distance_unit", [previous, {"value": new}])]


# ---- filter layout ------------------------------------------------------------------

def filter_layout(session: Session) -> list[dict]:
    return [{"key": item.key, "visible": item.visible} for item in load_layout(session)]


def set_filter_layout(session: Session, items: list[dict], expected: Optional[dict] = None) -> None:
    """Choose which filters the sidebar shows and their order: [{key, visible}],
    in display order. Filters left out follow the listed ones, shown."""
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("items must be a list of {key, visible}")
    if _skip(expected, filter_layout(session)):
        return
    save_layout(session, items)


def summarize_set_filter_layout(args: list[Any], session: Session) -> str:
    return "Change which filters the sidebar shows"


def undo_set_filter_layout(args: list[Any], session: Session) -> list[HostCall]:
    previous = filter_layout(session)
    new = _resolved(args[0])
    return [] if previous == new else [HostCall("set_filter_layout", [previous, {"value": new}])]


def _resolved(items: list[dict]) -> list[dict]:
    """The layout `items` produces once saved, as load_layout reads it back: unknown
    and repeated keys dropped, then the other filters appended, shown, in registry
    order."""
    keys = {f.key for f in FILTERS}
    listed, seen = [], set()
    for item in items if isinstance(items, list) else []:
        key = item.get("key") if isinstance(item, dict) else None
        if key in keys and key not in seen:
            listed.append({"key": key, "visible": bool(item.get("visible", True))})
            seen.add(key)
    return listed + [{"key": f.key, "visible": True} for f in FILTERS if f.key not in seen]
