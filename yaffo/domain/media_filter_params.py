"""The library filters as URL parameters, declared once.

Every screen with the filter panel (the gallery, albums' add screen, locations,
the sharing browser) reads its selections from the querystring, writes them back
into links, and hands them to apply_media_filters. The assistant builds gallery
links from the same filters. All of that is driven from MEDIA_FILTER_PARAMS, so
adding a filter means adding one entry here (plus its SQL in
media_filter_repository and its control in templates/filters/).

Values are keyed by `key` (the name apply_media_filters and the templates' `selected_<key>` use);
`param` is the querystring name.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from werkzeug.datastructures import MultiDict

from yaffo.common import MEDIA_TYPE_PHOTO, MEDIA_TYPE_VIDEO, SHAPES
from yaffo.distance_units import distance_to_kilometers

STR = "str"
INT = "int"
FLOAT = "float"
FLAG = "flag"  # 1 when set; the URL carries 1, the assistant passes true
INT_LIST = "int_list"
STR_LIST = "str_list"

MATCH_TYPES = ("any", "all")

# The gallery's two layouts, as the `view` parameter names them.
LIBRARY_VIEWS = ("grid", "timeline")

GALLERY_PATH = "/"
MEDIA_ITEM_PATH = "/media/view/{media_item_id}"


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


MEDIA_FILTER_PARAMS: tuple[FilterParam, ...] = (
    FilterParam("path", "path", STR, "Part of the file path, matched case-insensitively (e.g. a folder name)."),
    FilterParam("year", "year", INT, "Capture year."),
    FilterParam("month", "month", INT, "Capture month, 1-12.", choices=tuple(range(1, 13))),
    FilterParam("device", "device", STR, "Camera/device name exactly as stored (e.g. 'Apple iPhone 6')."),
    FilterParam("person", "person_ids", INT_LIST, "People ids; photos containing them."),
    FilterParam("person-match-type", "person_match_type", STR,
                "'any' of the people (default) or 'all' of them in the same photo.",
                choices=MATCH_TYPES, default="any"),
    FilterParam("gender", "gender", INT, "Only faces of this gender: 0 female, 1 male.", choices=(0, 1)),
    FilterParam("labels", "label_ids", INT_LIST, "Classification label ids (auto labels like 'beach')."),
    FilterParam("labels-match-type", "labels_match_type", STR, "'any' (default) or 'all' of the labels.",
                choices=MATCH_TYPES, default="any"),
    FilterParam("tag-name", "tag_name", STR, "A manual tag name."),
    FilterParam("tag-value", "tag_value", STR, "The tag's value, for name/value tags (with tag_name)."),
    FilterParam("location", "location_names", STR_LIST, "Location names exactly as stored."),
    FilterParam("location-match-type", "location_match_type", STR,
                "How location names combine; a photo has one location, so 'any' (default).",
                choices=MATCH_TYPES, default="any"),
    FilterParam("unnamed", "unnamed", FLAG, "Only items with no location name."),
    FilterParam("proximity-lat", "proximity_lat", FLOAT, "Latitude of a point to search around."),
    FilterParam("proximity-lon", "proximity_lon", FLOAT, "Longitude of a point to search around."),
    FilterParam("proximity-distance", "proximity_distance", FLOAT,
                "Radius around the point, in the distance unit chosen in Settings (km or miles).",
                filters_media=False),
    FilterParam("proximity-location", "proximity_location", STR,
                "A display name for the point (shown in the filter panel only).", filters_media=False),
    FilterParam("favorite", "favorite", FLAG, "Only favorites."),
    FilterParam("media-type", "media_type", STR, "'photo' or 'video'.", choices=(MEDIA_TYPE_PHOTO, MEDIA_TYPE_VIDEO)),
    FilterParam("shape", "shape", STR, "Orientation as displayed.", choices=tuple(SHAPES)),
)

_BY_KEY = {p.key: p for p in MEDIA_FILTER_PARAMS}


def _coerce(param: FilterParam, value: Any) -> Any:
    """One value in the parameter's type, or None when it doesn't fit."""
    try:
        if param.kind in (INT, INT_LIST, FLAG):
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
