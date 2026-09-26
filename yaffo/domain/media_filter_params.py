"""The library filters as URL parameters, declared once.

Every screen with the filter panel (the gallery, albums' add screen, locations,
the sharing browser) reads its selections from the querystring, writes them back
into links, and hands them to apply_media_filters. The assistant builds gallery
links from the same filters. All of that is driven from MEDIA_FILTER_PARAMS, so
adding a filter means adding one entry here (plus its SQL in
media_filter_repository and its control in templates/filters/).

Values are keyed by `key` (the name apply_media_filters and the templates'
`selected_<key>` use); `param` is the querystring name; `wire` is the name in a
sharing peer's list_files request (None when the parameter isn't sent: the
proximity radius travels converted, as `proximity_km`).

Driven from here: the filter panel's parsing and links (routes/filter_panel.py),
the sharing request both ways (routes/sharing.py, p2p/handlers/sharing.py), the
locations map's in-browser filtering (static/filters/client_filter.js, through
client_filter_config), the configurator's controls (routes/filter_config.py
names the parameters each control owns), and the assistant's link tool.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from werkzeug.datastructures import MultiDict

from yaffo.common import MEDIA_TYPE_PHOTO, MEDIA_TYPE_VIDEO, SHAPES
from yaffo.distance_units import (
    DISTANCE_UNIT_KILOMETERS,
    DISTANCE_UNIT_MILES,
    distance_to_kilometers,
    kilometers_per_unit,
)

STR = "str"
INT = "int"
FLOAT = "float"
FLAG = "flag"  # 1 when set, else None; the URL carries 1 (or true/on/yes), the assistant a bool
INT_LIST = "int_list"
STR_LIST = "str_list"

MATCH_TYPES = ("any", "all")

# The gallery's two layouts, as the `view` parameter names them.
LIBRARY_VIEWS = ("grid", "timeline")

# The proximity radius as a sharing request carries it: already in kilometers,
# so the two devices' unit settings needn't match.
WIRE_PROXIMITY_KM = "proximity_km"


@dataclass(frozen=True)
class FilterParam:
    param: str
    key: str
    kind: str
    description: str
    choices: Optional[tuple] = None
    default: Any = None
    # False for parameters that only carry display state (the proximity place
    # name) or need converting before they reach apply_media_filters.
    filters_media: bool = True
    wire: Optional[str] = None
    # Only qualifies another filter (a match type: any/all of the people). It
    # doesn't narrow anything by itself, so it never counts as an applied filter.
    modifier: bool = False


MEDIA_FILTER_PARAMS: tuple[FilterParam, ...] = (
    FilterParam("path", "path", STR, "Part of the file path, matched case-insensitively (e.g. a folder name).", wire="path"),
    FilterParam("year", "year", INT, "Capture year.", wire="year"),
    FilterParam("month", "month", INT, "Capture month, 1-12.", choices=tuple(range(1, 13)), wire="month"),
    FilterParam("device", "device", STR, "Camera/device name exactly as stored (e.g. 'Apple iPhone 6').", wire="device"),
    FilterParam("person", "person_ids", INT_LIST, "People ids; photos containing them.", wire="people"),
    FilterParam("person-match-type", "person_match_type", STR,
                "'any' of the people (default) or 'all' of them in the same photo.",
                choices=MATCH_TYPES, default="any", wire="person_match_type", modifier=True),
    FilterParam("gender", "gender", INT, "Only faces of this gender: 0 female, 1 male.", choices=(0, 1), wire="gender"),
    FilterParam("labels", "label_ids", INT_LIST, "Classification label ids (auto labels like 'beach').", wire="labels"),
    FilterParam("labels-match-type", "labels_match_type", STR, "'any' (default) or 'all' of the labels.",
                choices=MATCH_TYPES, default="any", wire="labels_match_type", modifier=True),
    FilterParam("tag-name", "tag_name", STR, "A manual tag name.", wire="tag_name"),
    FilterParam("tag-value", "tag_value", STR, "The tag's value, for name/value tags (with tag_name).", wire="tag_value"),
    FilterParam("location", "location_names", STR_LIST, "Location names exactly as stored.", wire="locations"),
    FilterParam("location-match-type", "location_match_type", STR,
                "How location names combine; a photo has one location, so 'any' (default).",
                choices=MATCH_TYPES, default="any", wire="location_match_type", modifier=True),
    FilterParam("unnamed", "unnamed", FLAG, "Only items with no location name.", wire="unnamed"),
    FilterParam("proximity-lat", "proximity_lat", FLOAT, "Latitude of a point to search around.", wire="proximity_lat"),
    FilterParam("proximity-lon", "proximity_lon", FLOAT, "Longitude of a point to search around.", wire="proximity_lon"),
    FilterParam("proximity-distance", "proximity_distance", FLOAT,
                "Radius around the point, in the distance unit chosen in Settings (km or miles).",
                filters_media=False),
    FilterParam("proximity-location", "proximity_location", STR,
                "A display name for the point (shown in the filter panel only).", filters_media=False),
    FilterParam("favorite", "favorite", FLAG, "Only favorites.", wire="favorite"),
    FilterParam("media-type", "media_type", STR, "'photo' or 'video'.", choices=(MEDIA_TYPE_PHOTO, MEDIA_TYPE_VIDEO), wire="media_type"),
    FilterParam("shape", "shape", STR, "Orientation as displayed.", choices=tuple(SHAPES), wire="shape"),
)

_BY_KEY = {p.key: p for p in MEDIA_FILTER_PARAMS}


_FLAG_ON = frozenset({"1", "true", "on", "yes"})


def _coerce(param: FilterParam, value: Any) -> Any:
    """One value in the parameter's type, or None when it doesn't fit."""
    if param.kind == FLAG:
        # On/off: 1 when set, None otherwise. A URL may spell it 1/true/on/yes
        # (the form sends 1); the assistant passes a JSON bool.
        if isinstance(value, bool) or isinstance(value, int):
            return 1 if value else None
        text = str(value).strip().lower()
        return 1 if text in _FLAG_ON else None
    try:
        if param.kind in (INT, INT_LIST):
            if isinstance(value, bool):
                value = int(value)
            value = int(value)
        elif param.kind == FLOAT:
            value = float(value)
        else:
            value = str(value).strip() or None
    except (TypeError, ValueError):
        return None
    if value is not None and param.choices is not None and value not in param.choices:
        return None
    return value


def default_values() -> dict[str, Any]:
    """Every selection unset: None, an empty list, or the parameter's default."""
    return {
        param.key: [] if param.kind in (INT_LIST, STR_LIST) else param.default
        for param in MEDIA_FILTER_PARAMS
    }


def parse_filter_params(args: MultiDict) -> dict[str, Any]:
    """The selections in a querystring, by key. Missing or invalid values are
    None (a list is empty; a match type falls back to 'any')."""
    values: dict[str, Any] = {}
    for param in MEDIA_FILTER_PARAMS:
        if param.kind in (INT_LIST, STR_LIST):
            items = (_coerce(param, raw) for raw in args.getlist(param.param))
            values[param.key] = [item for item in items if item is not None]
        else:
            raw = args.get(param.param)
            value = _coerce(param, raw) if raw is not None else None
            values[param.key] = param.default if value is None else value
    return values


def filter_query_params(values: Mapping[str, Any]) -> dict[str, Any]:
    """Selections (by key) as querystring parameters, for links and forms."""
    return {param.param: values.get(param.key) for param in MEDIA_FILTER_PARAMS}


def media_filter_selections(values: Mapping[str, Any], distance_unit: str) -> dict[str, Any]:
    """Selections (by key) as apply_media_filters takes them: the proximity radius
    converted to kilometers, display-only parameters dropped."""
    selections = {param.key: values.get(param.key) for param in MEDIA_FILTER_PARAMS if param.filters_media}
    distance = values.get("proximity_distance")
    selections["proximity_km"] = distance_to_kilometers(distance, distance_unit) if distance else None
    return selections


def filter_values_from_json(filters: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate filters given by key (the assistant's tool input): every value
    (defaults for what wasn't given, as parse_filter_params does), and a message
    for each given value that didn't fit."""
    values = default_values()
    errors: list[str] = []
    for key, raw in filters.items():
        param = _BY_KEY.get(key)
        if param is None:
            errors.append(f"unknown filter {key!r}")
            continue
        if param.kind in (INT_LIST, STR_LIST):
            items = raw if isinstance(raw, list) else [raw]
            coerced = [_coerce(param, item) for item in items]
            if any(item is None for item in coerced):
                errors.append(f"{key} has an invalid value")
                continue
            values[key] = coerced
        else:
            value = _coerce(param, raw)
            if value is None:
                allowed = f" (one of {', '.join(map(str, param.choices))})" if param.choices else ""
                errors.append(f"{key} has an invalid value{allowed}")
                continue
            values[key] = (value or None) if param.kind == FLAG else value
    return values, errors


def filters_json_schema() -> dict:
    """The filters as a JSON-schema object, keyed by `key`, for a tool input."""
    properties: dict[str, dict] = {}
    for param in MEDIA_FILTER_PARAMS:
        if param.kind == INT_LIST:
            schema: dict[str, Any] = {"type": "array", "items": {"type": "integer"}}
        elif param.kind == STR_LIST:
            schema = {"type": "array", "items": {"type": "string"}}
        elif param.kind == FLAG:
            schema = {"type": "boolean"}
        else:
            schema = {"type": {INT: "integer", FLOAT: "number", STR: "string"}[param.kind]}
        if param.choices is not None:
            schema["enum"] = list(param.choices)
        schema["description"] = param.description
        properties[param.key] = schema
    return {"type": "object", "properties": properties, "additionalProperties": False}


# ---- sharing: the list_files request between devices -------------------------

_BY_WIRE = {p.wire: p for p in MEDIA_FILTER_PARAMS if p.wire is not None}


def wire_filters(values: Mapping[str, Any], distance_unit: str) -> dict[str, Any]:
    """Selections (by key) as a list_files request's `filters`: only what's set,
    by wire name. The proximity search goes only when complete, with the radius
    converted to kilometers with this device's unit."""
    payload: dict[str, Any] = {}
    for param in MEDIA_FILTER_PARAMS:
        value = values.get(param.key)
        if param.wire is None or value in (None, [], "") or value == param.default:
            continue
        payload[param.wire] = True if param.kind == FLAG else value
    lat, lon = values.get("proximity_lat"), values.get("proximity_lon")
    distance = values.get("proximity_distance")
    if lat is not None and lon is not None and distance:
        payload[WIRE_PROXIMITY_KM] = distance_to_kilometers(distance, distance_unit)
    else:
        payload.pop("proximity_lat", None)
        payload.pop("proximity_lon", None)
    return payload


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _wire_value_ok(param: FilterParam, value: Any) -> bool:
    if param.kind == INT:
        ok = _is_int(value)
    elif param.kind == FLOAT:
        ok = _is_number(value)
    elif param.kind == FLAG:
        ok = isinstance(value, (bool, int))
    elif param.kind == STR:
        ok = isinstance(value, str)
    elif param.kind == INT_LIST:
        return isinstance(value, list) and all(_is_int(item) for item in value)
    else:
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return ok and (param.choices is None or value in param.choices)


def validate_wire_filters(filters: Any) -> Optional[str]:
    """Why a list_files request's `filters` are unacceptable, or None."""
    if not isinstance(filters, dict):
        return "filters must be an object"
    unknown = set(filters) - set(_BY_WIRE) - {WIRE_PROXIMITY_KM}
    if unknown:
        return f"unknown filters: {', '.join(sorted(unknown))}"
    for name, value in filters.items():
        ok = _is_number(value) if name == WIRE_PROXIMITY_KM else _wire_value_ok(_BY_WIRE[name], value)
        if not ok:
            return f"invalid value for filter {name!r}"
    return None


def selections_from_wire(filters: Mapping[str, Any]) -> dict[str, Any]:
    """A validated list_files request's `filters` as apply_media_filters takes
    them (unset filters at their defaults)."""
    values = default_values()
    for name, value in filters.items():
        param = _BY_WIRE.get(name)
        if param is None:
            continue
        values[param.key] = _coerce(param, value) if param.kind in (STR, FLAG) else value
    selections = {param.key: values[param.key] for param in MEDIA_FILTER_PARAMS if param.filters_media}
    selections["proximity_km"] = filters.get(WIRE_PROXIMITY_KM)
    return selections


# ---- the locations map's in-browser filtering --------------------------------

def client_filter_config() -> dict[str, Any]:
    """What static/filters/client_filter.js needs to read the filter form the
    way the server does: each parameter's form name, key, kind, allowed values
    and default, plus kilometers per distance unit."""
    return {
        "params": [
            {
                "param": param.param,
                "key": param.key,
                "kind": param.kind,
                "choices": list(param.choices) if param.choices is not None else None,
                "default": param.default,
            }
            for param in MEDIA_FILTER_PARAMS
        ],
        "kilometers_per_unit": {
            unit: kilometers_per_unit(unit) for unit in (DISTANCE_UNIT_KILOMETERS, DISTANCE_UNIT_MILES)
        },
    }


def applied_keys(values: Mapping[str, Any]) -> set[str]:
    """The keys of the filters these values actually apply: set to something
    other than the default, modifiers aside."""
    defaults = default_values()
    return {
        param.key for param in MEDIA_FILTER_PARAMS
        if not param.modifier and values.get(param.key) not in (None, [], "", defaults[param.key])
    }

