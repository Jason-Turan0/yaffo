"""Link tools: the assistant points the user at places in the app.

- link_to_photos: the gallery with filters applied (people, year, location, …),
  built from the same filter table the filter panel uses
  (domain/media_filter_params.py), so every filter the panel has, the assistant
  has, with the same names and validation.
- link_to_page: any page in the app (a person's faces, an album, one photo,
  Settings, an automation, …), from the page table built off the Flask routes
  (app_pages.py).

Both check what they link to (the people, labels, photos and albums exist; how
many items match) so the model never offers a dead or empty link. URLs are built
with werkzeug's URL builder from the real route rules. The link goes to the chat
as structured data and is shown under the answer; the model is told not to write
URLs itself.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlencode

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
from yaffo.site_agents.assistant.schemas import AppLink, ToolActivity
from yaffo.site_agents.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
    ToolResult,
)

LINK_TO_PHOTOS = "link_to_photos"
LINK_TO_PAGE = "link_to_page"
MAX_TITLE_CHARS = 80
GALLERY_ENDPOINT = "index"

# Page parameters that name a record, checked to exist before linking.
_RECORD_FOR_ARGUMENT = {
    "media_item_id": MediaItem,
    "person_id": Person,
    "album_id": Album,
    "page_id": CustomPage,
}

_TITLE = {
    "type": "string",
    "description": "The link text the user sees, in their language, e.g. 'Chase in 2019'.",
}

_PHOTOS_SCHEMA = {
    "type": "object",
    "properties": {
        "title": _TITLE,
        "filters": filters_json_schema(),
        "view": {"type": "string", "enum": list(LIBRARY_VIEWS), "description": "Gallery layout; omit to keep the user's."},
    },
    "required": ["title", "filters"],
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


def gallery_url(values: dict[str, Any], view: Optional[str] = None) -> str:
    """The gallery with these filter values (by key). Empty values and defaults
    are left out, since the gallery fills them in."""
    defaults = {param.param: param.default for param in MEDIA_FILTER_PARAMS}
    params = {
        name: value for name, value in filter_query_params(values).items()
        if value not in (None, [], "") and value != defaults[name]
    }
    if view in LIBRARY_VIEWS:
        params["view"] = view
    return page_url(GALLERY_ENDPOINT, params)


def _title(args: dict) -> str:
    return " ".join(str(args.get("title") or "").split())[:MAX_TITLE_CHARS]


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
                "run_script or data_query results; the link appears under your answer.",
                _PHOTOS_SCHEMA,
            ),
            RawToolDefinition(
                LINK_TO_PAGE,
                "Give the user a link to a page in the app. The link appears under your answer. "
                "Look up ids first (people, albums, media items) with a script. Pages:\n" + _page_catalog(),
                _page_schema(),
            ),
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        if name == LINK_TO_PHOTOS:
            return self._photos(args)
        if name == LINK_TO_PAGE:
            return self._page(args)
        raise ValueError(f"Unknown tool: {name}")

    def _photos(self, args: dict) -> ToolResult:
        title = _title(args) or "Photos"
        raw_filters = args.get("filters")
        filters: dict[str, Any] = raw_filters if isinstance(raw_filters, dict) else {}
        values, errors = filter_values_from_json(filters)
        missing_people = _missing_ids(self.session, Person, values.get("person_ids", []))
        missing_labels = _missing_ids(self.session, ClassificationLabel, values.get("label_ids", []))
        if missing_people:
            errors.append(f"no people with ids {missing_people}")
        if missing_labels:
            errors.append(f"no labels with ids {missing_labels}")
        if errors:
            return self._result(LINK_TO_PHOTOS, title, None, "Couldn't build the link: " + "; ".join(errors) + ".")

        selections = media_filter_selections(values, get_saved_distance_unit(self.session))
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
            "don't repeat the URL.",
            count=count)

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

    def _result(self, tool: str, title: str, url: Optional[str], text: str, count: int = 0) -> ToolResult:
        activity = ToolActivity(
            tool=tool, args={"title": title}, count=count, error=url is None,
            links=[AppLink(title=title, url=url)] if url else [],
        )
        return ToolResult(model_text=text, host_data=activity.to_dict())
