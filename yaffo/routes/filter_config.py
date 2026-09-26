"""Sidebar filter layout: which filters appear in the panel and in what order.
One layout shared by every page that renders the configurable panel (home
gallery, locations map, albums add, remote gallery), persisted in
ApplicationSettings (name=filter_layout) as a JSON list of {key, visible}.

FILTERS is the source of truth for the available filter *controls* (key -> label +
template + the parameters it sets, which are declared in
domain/media_filter_params.py; every parameter belongs to exactly one control).
The saved layout is *merged* onto it on read: known keys keep their saved order and
visibility, unknown saved keys are dropped, and any registry filter missing from the
saved layout is appended (visible) — so adding a filter here makes it show up without
a migration, and removing one drops it cleanly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from flask import Flask, request
from flask_babel import gettext
from sqlalchemy.orm import Session

from werkzeug.datastructures import MultiDict

from yaffo.db import db
from yaffo.db.models import ApplicationSettings
from yaffo.domain.media_filter_params import applied_keys, parse_filter_params

SETTING_NAME = "filter_layout"


@dataclass(frozen=True)
class FilterDef:
    """A configurable filter control: its stable key, sidebar label, include
    template, and the filter parameters (media_filter_params keys) it sets."""
    key: str
    label: str
    template: str
    params: tuple[str, ...]


# Registry + default order (matches the historical sidebar order).
FILTERS: list[FilterDef] = [
    FilterDef("year", "Year", "filters/_year.html", ("year",)),
    FilterDef("month", "Month", "filters/_month.html", ("month",)),
    FilterDef("path", "File", "filters/_path.html", ("path",)),
    FilterDef("people", "People", "filters/_people.html", ("person_ids", "person_match_type")),
    FilterDef("gender", "Gender", "filters/_gender.html", ("gender",)),
    FilterDef("labels", "Label", "filters/_labels.html", ("label_ids", "labels_match_type")),
    FilterDef("tags", "Tags", "filters/_tags.html", ("tag_name", "tag_value")),
    FilterDef("locations", "Locations", "filters/_locations.html", (
        "location_names", "location_match_type", "unnamed",
        "proximity_lat", "proximity_lon", "proximity_distance", "proximity_location",
    )),
    FilterDef("device", "Device", "filters/_device.html", ("device",)),
    FilterDef("favorite", "Favorites", "filters/_favorite.html", ("favorite",)),
    FilterDef("media_type", "Media Type", "filters/_media_type.html", ("media_type",)),
    FilterDef("shape", "Shape", "filters/_shape.html", ("shape",)),
]
_BY_KEY = {f.key: f for f in FILTERS}


@dataclass(frozen=True)
class FilterLayoutItem:
    """One row of the resolved layout the sidebar/modal render from."""
    key: str
    label: str
    template: str
    visible: bool


def applied_count(args: MultiDict) -> int:
    """How many filter controls the URL narrows by: the "N filters applied"
    badge on the gallery-panel pages. A control counts once however many of its
    parameters or values are set (two people, a place plus radius)."""
    applied = applied_keys(parse_filter_params(args))
    return sum(1 for control in FILTERS if applied.intersection(control.params))


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
    and de-duping. Stored as JSON on the shared ApplicationSettings row."""
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


def init_filter_config_routes(app: Flask):
    app.add_template_global(applied_count, "applied_gallery_filter_count")

    @app.route("/settings/filters", methods=["POST"])
    def save_filter_layout():
        """Persist the shared filter layout (order + visibility);
        body is {"items": [{key, visible}, ...]}."""
        payload = request.get_json(silent=True) or {}
        items = payload.get("items")
        if not isinstance(items, list):
            return {
                "error": gettext("Items must be a list"),
                "code": "items_must_be_list",
            }, 400
        save_layout(db.session, items)
        return "", 204
