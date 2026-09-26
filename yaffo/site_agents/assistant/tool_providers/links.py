"""Link tools: the assistant points the user at places in the app.

- link_to_photos: the gallery with filters applied (people, year, location, …),
  built from the same filter table the filter panel uses
  (domain/media_filter_params.py), so every filter the panel has, the assistant
  has, with the same names and validation. It can also name exact items
  (media_item_ids, a URL-only filter), e.g. the ones a script found. The same
  filters open on the locations map with page="map" (it applies them on load).
- link_to_page: any page in the app (a person's faces, an album, one photo,
  Settings, an automation, …), from the page table built off the Flask routes
  (app_pages.py).
- link_to_file: a button that opens a file or folder on the user's computer, named
  by a media item id or a media folder id plus a relative path. Yaffo resolves the
  real path (file_targets.py); the model only ever sees the folder label.

Both check what they link to (the people, labels, photos and albums exist; how
many items match) so the model never offers a dead or empty link. URLs are built
with werkzeug's URL builder from the real route rules. The link goes to the chat
as structured data and is shown under the answer; the model is told not to write
URLs itself.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from yaffo.db.models import Album, ClassificationLabel, CustomPage, MediaItem, Person
from yaffo.db.repositories.media_filter_repository import apply_media_filters
from yaffo.distance_units import get_saved_distance_unit
from yaffo.domain.media_filter_params import (
    LIBRARY_VIEWS,
    MEDIA_FILTER_PARAMS,
    filter_query_params,
    filter_values_from_json,
    filters_json_schema,
    media_filter_selections,
)
from yaffo.site_agents.assistant.app_pages import app_pages, page_url
from yaffo.site_agents.assistant.file_targets import (
    SHOW_FILE, SHOW_FOLDER, SHOW_OPTIONS, FileTarget, TargetError, resolve_target,
)
from yaffo.site_agents.assistant.schemas import AppLink, OpenLink, ToolActivity
from yaffo.site_agents.common.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
    ToolResult,
)

LINK_TO_PHOTOS = "link_to_photos"
LINK_TO_PAGE = "link_to_page"
LINK_TO_FILE = "link_to_file"
MAX_TITLE_CHARS = 80
# Exact items one link may name; each is a querystring value, so this keeps the URL short.
MAX_LINK_ITEMS = 500
GALLERY_ENDPOINT = "index"
MAP_ENDPOINT = "locations_list"
PAGE_GALLERY = "gallery"
PAGE_MAP = "map"

# Page parameters that name a record, checked to exist before linking.
_RECORD_FOR_ARGUMENT = {
    "media_item_id": MediaItem,
    "person_id": Person,
    "album_id": Album,
    "page_id": CustomPage,
}

_TITLE = {
    "type": "string",
    "description": "The link text the user sees, in their language, e.g. 'Billy in 2019'.",
}

_PHOTOS_SCHEMA = {
    "type": "object",
    "properties": {
        "title": _TITLE,
        "filters": filters_json_schema(),
        "view": {"type": "string", "enum": list(LIBRARY_VIEWS), "description": "Gallery layout; omit to keep the user's."},
        "page": {
            "type": "string", "enum": [PAGE_GALLERY, PAGE_MAP],
            "description": "'gallery' (default), or 'map' for the locations map, which shows only the "
                           "matching items that have GPS coordinates.",
        },
    },
    "required": ["title", "filters"],
    "additionalProperties": False,
}


_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": _TITLE,
        "media_item_id": {"type": "integer", "description": "A photo or video, by its media item id."},
        "media_dir_id": {"type": "string", "description": "Or a media folder id, from a [media folder <id>] label."},
        "path": {
            "type": "string",
            "description": "With media_dir_id: the path inside it, as in the label ('' for the folder itself).",
        },
        "show": {
            "type": "string", "enum": list(SHOW_OPTIONS),
            "description": f"'{SHOW_FILE}' opens it with its default app; '{SHOW_FOLDER}' shows it in its folder.",
        },
    },
    "required": ["title", "show"],
    "additionalProperties": False,
}


def _page_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "title": _TITLE,
            "page": {"type": "string", "enum": sorted(app_pages()), "description": "The page, from the list above."},
            "values": {
                "type": "object",
                "description": "The page's parameters, e.g. {\"person_id\": 10}. Omit for a page without any.",
                "additionalProperties": {"type": ["integer", "string"]},
            },
        },
        "required": ["title", "page"],
        "additionalProperties": False,
    }


def _page_catalog() -> str:
    """The pages as the tool description lists them: name(parameters): what it is."""
    lines = []
    for page in sorted(app_pages().values(), key=lambda p: p.endpoint):
        params = ", ".join(page.arguments)
        lines.append(f"- {page.endpoint}{f'({params})' if params else ''}: {page.description}")
    return "\n".join(lines)


def _set_params(values: dict[str, Any]) -> dict[str, Any]:
    """Filter values (by key) as querystring parameters, leaving out empty values
    and defaults, since the page fills them in."""
    defaults = {param.param: param.default for param in MEDIA_FILTER_PARAMS}
    return {
        name: value for name, value in filter_query_params(values).items()
        if value not in (None, [], "") and value != defaults[name]
    }


def gallery_url(values: dict[str, Any], view: Optional[str] = None) -> str:
    """The gallery with these filter values (by key)."""
    params = _set_params(values)
    if view in LIBRARY_VIEWS:
        params["view"] = view
    return page_url(GALLERY_ENDPOINT, params)


def map_url(values: dict[str, Any]) -> str:
    """The locations map with these filter values (by key)."""
    return page_url(MAP_ENDPOINT, _set_params(values))


def _title(args: dict) -> str:
    return " ".join(str(args.get("title") or "").split())[:MAX_TITLE_CHARS]


def _skipped(missing_items: list[int]) -> str:
    if not missing_items:
        return ""
    more = "…" if len(missing_items) > 20 else ""
    return f" {len(missing_items)} of the media_item_ids don't exist and were ignored: {missing_items[:20]}{more}."


def _missing_ids(session: Session, model: Any, ids: list[int]) -> list[int]:
    if not ids:
        return []
    found = {row[0] for row in session.query(model.id).filter(model.id.in_(ids))}
    return [i for i in ids if i not in found]


class LinkToolProvider(ToolProvider):
    def __init__(self, session: Session):
        self.session = session

    def get_tools(self) -> list[RawToolDefinition]:
        return [
            RawToolDefinition(
                LINK_TO_PHOTOS,
                "Give the user a link to the photo gallery filtered to what they asked about (people, "
                "dates, places, labels, tags, favorites, …). Returns how many items match. Use ids from "
                "run_script or data_query results; the link appears under your answer. To show exactly the "
                f"items a script found, pass their ids as media_item_ids (at most {MAX_LINK_ITEMS}); other "
                "filters then narrow within them. With page='map' the same filters open on the locations map.",
                _PHOTOS_SCHEMA,
            ),
            RawToolDefinition(
                LINK_TO_PAGE,
                "Give the user a link to a page in the app. The link appears under your answer. "
                "Look up ids first (people, albums, media items) with a script. Pages:\n" + _page_catalog(),
                _page_schema(),
            ),
            RawToolDefinition(
                LINK_TO_FILE,
                "Give the user a button that opens a file or folder on their computer, e.g. a photo "
                "that failed to index, or a media folder to check. Name it by media_item_id, or by "
                "media_dir_id and path from a [media folder <id>]/path label. Yaffo checks it exists; "
                "the button appears under your answer. It opens only when the user clicks it.",
                _FILE_SCHEMA,
            ),
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        if name == LINK_TO_PHOTOS:
            return self._photos(args)
        if name == LINK_TO_PAGE:
            return self._page(args)
        if name == LINK_TO_FILE:
            return self._file(args)
        raise ValueError(f"Unknown tool: {name}")

    def _photos(self, args: dict) -> ToolResult:
        title = _title(args) or "Photos"
        raw_filters = args.get("filters")
        filters: dict[str, Any] = raw_filters if isinstance(raw_filters, dict) else {}
        values, errors = filter_values_from_json(filters)
        item_ids = list(dict.fromkeys(values.get("media_item_ids") or []))
        values["media_item_ids"] = item_ids
        if len(item_ids) > MAX_LINK_ITEMS:
            errors.append(f"media_item_ids can list at most {MAX_LINK_ITEMS} items (got {len(item_ids)}); "
                          "narrow with other filters, or make several links")
        missing_items = _missing_ids(self.session, MediaItem, item_ids) if not errors else []
        if item_ids and len(missing_items) == len(item_ids):
            errors.append("none of the media_item_ids exist")
        # Unknown ids are dropped from the link; dropping them all would link the whole
        # library, hence the error above.
        values["media_item_ids"] = [i for i in item_ids if i not in missing_items]
        missing_people = _missing_ids(self.session, Person, values.get("person_ids", []))
        missing_labels = _missing_ids(self.session, ClassificationLabel, values.get("label_ids", []))
        if missing_people:
            errors.append(f"no people with ids {missing_people}")
        if missing_labels:
            errors.append(f"no labels with ids {missing_labels}")
        on_map = args.get("page") == PAGE_MAP
        if on_map and args.get("view"):
            errors.append("the map has no view; leave it out")
        if errors:
            return self._result(LINK_TO_PHOTOS, title, None, "Couldn't build the link: " + "; ".join(errors) + ".")

        selections = media_filter_selections(values, get_saved_distance_unit(self.session))
        if on_map:
            return self._map_link(title, values, selections, missing_items)
        count = apply_media_filters(self.session, self.session.query(MediaItem), selections).count()
        if count == 0:
            return self._result(
                LINK_TO_PHOTOS, title, None,
                "No items match these filters, so no link was made. Check the filters, or tell the user.",
                count=0)
        url = gallery_url(values, args.get("view"))
        return self._result(
            LINK_TO_PHOTOS, title, url,
            f"Link ready ({count} item(s) match). It's shown under your answer as “{title}”; "
            f"don't repeat the URL.{_skipped(missing_items)}",
            count=count)

    def _map_link(self, title: str, values: dict[str, Any], selections: dict[str, Any],
                  missing_items: list[int]) -> ToolResult:
        """The map only shows items with coordinates, so the count is of those."""
        matching = apply_media_filters(self.session, self.session.query(MediaItem), selections)
        total = matching.count()
        placed = matching.filter(MediaItem.latitude.isnot(None), MediaItem.longitude.isnot(None)).count()
        if placed == 0:
            reason = "None of the matching items have GPS coordinates" if total else "No items match these filters"
            return self._result(
                LINK_TO_PHOTOS, title, None,
                f"{reason}, so nothing would show on the map and no link was made."
                + (" Offer a gallery link instead." if total else ""), count=0)
        unplaced = total - placed
        note = f" {unplaced} matching item(s) have no GPS coordinates and won't appear on the map." if unplaced else ""
        return self._result(
            LINK_TO_PHOTOS, title, map_url(values),
            f"Map link ready ({placed} item(s) on the map). It's shown under your answer as “{title}”; "
            f"don't repeat the URL.{note}{_skipped(missing_items)}",
            count=placed)

    def _page(self, args: dict) -> ToolResult:
        endpoint = str(args.get("page") or "")
        title = _title(args) or endpoint
        page = app_pages().get(endpoint)
        if page is None:
            return self._result(LINK_TO_PAGE, title, None, f"No page named {endpoint!r}; pick one from the list.")
        raw_values = args.get("values")
        values: dict[str, Any] = raw_values if isinstance(raw_values, dict) else {}
        errors = []
        coerced: dict[str, Any] = {}
        for name, converter in page.arguments.items():
            value = values.get(name)
            if value is None or value == "":
                errors.append(f"{name} is required")
            elif converter == "int":
                try:
                    coerced[name] = int(value)
                except (TypeError, ValueError):
                    errors.append(f"{name} must be a number")
            elif "/" in str(value):
                errors.append(f"{name} can't contain '/'")
            else:
                coerced[name] = str(value)
        extra = sorted(set(values) - set(page.arguments))
        if extra:
            errors.append(f"{endpoint} takes no {', '.join(extra)}")
        for name, value in coerced.items():
            record = _RECORD_FOR_ARGUMENT.get(name)
            if record is not None and self.session.get(record, value) is None:
                errors.append(f"no {name.removesuffix('_id').replace('_', ' ')} with id {value}")
        if errors:
            return self._result(LINK_TO_PAGE, title, None, "Couldn't build the link: " + "; ".join(errors) + ".")
        return self._result(
            LINK_TO_PAGE, title, page_url(endpoint, coerced),
            f"Link ready. It's shown under your answer as “{title}”; don't repeat the URL.", count=1)

    def _file(self, args: dict) -> ToolResult:
        title = _title(args) or "Open"
        try:
            target = FileTarget.from_dict({k: args.get(k) for k in ("show", "media_item_id", "media_dir_id", "path")})
            resolved = resolve_target(self.session, target)
        except TargetError as exc:
            activity = ToolActivity(tool=LINK_TO_FILE, args={"title": title}, error=True)
            return ToolResult(model_text=f"Couldn't make the button: {exc}.", host_data=activity.to_dict())
        kind = "folder" if resolved.is_dir else "file"
        verb = "shows it in its folder" if target.show == SHOW_FOLDER and not resolved.is_dir else "opens it"
        activity = ToolActivity(
            tool=LINK_TO_FILE, args={"title": title}, count=1,
            opens=[OpenLink(title=title, show=target.show, target=target.to_dict())],
        )
        return ToolResult(
            model_text=(f"Button ready: “{title}” {verb} ({kind} {resolved.label}) when the user clicks it. "
                        "It's shown under your answer; don't repeat the path."),
            host_data=activity.to_dict())

    def _result(self, tool: str, title: str, url: Optional[str], text: str, count: int = 0) -> ToolResult:
        activity = ToolActivity(
            tool=tool, args={"title": title}, count=count, error=url is None,
            links=[AppLink(title=title, url=url)] if url else [],
        )
        return ToolResult(model_text=text, host_data=activity.to_dict())
