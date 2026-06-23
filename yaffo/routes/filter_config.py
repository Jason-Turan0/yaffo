"""Home-page filter layout: which filters appear in the gallery sidebar and in what
order. Persisted for this page only, in ApplicationSettings (name=SETTING_NAME) as a
JSON list of {key, visible}.

FILTERS is the source of truth for the available filters (key -> label + template).
The saved layout is *merged* onto it on read: known keys keep their saved order and
visibility, unknown saved keys are dropped, and any registry filter missing from the
saved layout is appended (visible) — so adding a filter here makes it show up without
a migration, and removing one drops it cleanly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from yaffo.db.models import ApplicationSettings

SETTING_NAME = "home_filter_layout"


@dataclass(frozen=True)
class FilterDef:
    """A configurable filter: its stable key, sidebar label, and include template."""
    key: str
    label: str
    template: str


# Registry + default order (matches the historical sidebar order).
FILTERS: list[FilterDef] = [
    FilterDef("year", "Year", "filters/_year.html"),
    FilterDef("month", "Month", "filters/_month.html"),
    FilterDef("path", "File", "filters/_path.html"),
    FilterDef("people", "People", "filters/_people.html"),
    FilterDef("gender", "Gender", "filters/_gender.html"),
    FilterDef("labels", "Label", "filters/_labels.html"),
    FilterDef("tags", "Tags", "filters/_tags.html"),
    FilterDef("locations", "Locations", "filters/_locations.html"),
    FilterDef("device", "Device", "filters/_device.html"),
    FilterDef("favorite", "Favorites", "filters/_favorite.html"),
    FilterDef("media_type", "Media Type", "filters/_media_type.html"),
]
_BY_KEY = {f.key: f for f in FILTERS}


@dataclass(frozen=True)
class FilterLayoutItem:
    """One row of the resolved layout the sidebar/modal render from."""
    key: str
    label: str
    template: str
    visible: bool


def default_keys() -> list[str]:
    """The registry order — what 'Reset to defaults' restores (all visible)."""
    return [f.key for f in FILTERS]


def _saved(session: Session) -> list[dict]:
    setting = session.query(ApplicationSettings).filter_by(name=SETTING_NAME).first()
    if not setting or not setting.value:
        return []
    try:
        data = json.loads(setting.value)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def load_layout(session: Session) -> list[FilterLayoutItem]:
    """The resolved layout: saved order/visibility for known keys, then any registry
    filter not yet saved appended (visible). Defaults to all filters visible in
    registry order when nothing is saved."""
    items: list[FilterLayoutItem] = []
    seen: set[str] = set()
    for entry in _saved(session):
        key = entry.get("key") if isinstance(entry, dict) else None
        f = _BY_KEY.get(key)
        if f and key not in seen:
            items.append(FilterLayoutItem(f.key, f.label, f.template, bool(entry.get("visible", True))))
            seen.add(key)
    for f in FILTERS:
        if f.key not in seen:
            items.append(FilterLayoutItem(f.key, f.label, f.template, True))
    return items


def save_layout(session: Session, items: list[dict]) -> None:
    """Persist [{key, visible}] (list order = display order), keeping only known keys
    and de-duping. Stored as JSON on the ApplicationSettings row."""
    cleaned: list[dict] = []
    seen: set[str] = set()
    for entry in items:
        key = entry.get("key") if isinstance(entry, dict) else None
        if key in _BY_KEY and key not in seen:
            cleaned.append({"key": key, "visible": bool(entry.get("visible", True))})
            seen.add(key)
    setting = session.query(ApplicationSettings).filter_by(name=SETTING_NAME).first()
    if setting is None:
        session.add(ApplicationSettings(name=SETTING_NAME, type="json", value=json.dumps(cleaned)))
    else:
        setting.value = json.dumps(cleaned)
    session.commit()
