"""Link tools: the assistant points the user at photos in the app.

- link_to_photos: the gallery with filters applied (people, year, location, …),
  built from the same filter table the filter panel uses
  (domain/media_filter_params.py), so every filter the panel has, the assistant
  has, with the same names and validation.
- link_to_media_item: one photo or video's detail page.

Both check what they link to (the item exists, the people and labels exist, how
many items match) so the model never offers a dead or empty link. The link goes
to the chat as structured data and is shown under the answer; the model is told
not to write URLs itself.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from yaffo.db.models import ClassificationLabel, MediaItem, Person
from yaffo.db.repositories.media_filter_repository import apply_media_filters
from yaffo.distance_units import get_saved_distance_unit
from yaffo.domain.media_filter_params import (
    GALLERY_PATH,
    LIBRARY_VIEWS,
    MEDIA_FILTER_PARAMS,
    MEDIA_ITEM_PATH,
    filter_query_params,
    filter_values_from_json,
    filters_json_schema,
    media_filter_selections,
)
from yaffo.site_agents.assistant.schemas import AppLink, ToolActivity
from yaffo.site_agents.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
    ToolResult,
)

LINK_TO_PHOTOS = "link_to_photos"
LINK_TO_MEDIA_ITEM = "link_to_media_item"
MAX_TITLE_CHARS = 80

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

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {"title": _TITLE, "media_item_id": {"type": "integer"}},
    "required": ["title", "media_item_id"],
    "additionalProperties": False,
}


def gallery_url(values: dict[str, Any], view: Optional[str] = None) -> str:
    """The gallery path with these filter values (by key). Empty values and
    defaults are left out, since the gallery fills them in."""
    defaults = {param.param: param.default for param in MEDIA_FILTER_PARAMS}
    params = {
        name: value for name, value in filter_query_params(values).items()
        if value not in (None, [], "") and value != defaults[name]
    }
    if view in LIBRARY_VIEWS:
        params["view"] = view
    query = urlencode(params, doseq=True)
    return f"{GALLERY_PATH}?{query}" if query else GALLERY_PATH


def media_item_url(media_item_id: int) -> str:
    return MEDIA_ITEM_PATH.format(media_item_id=media_item_id)


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
                LINK_TO_MEDIA_ITEM,
                "Give the user a link to one photo or video's detail page, by its media item id. "
                "The link appears under your answer.",
                _ITEM_SCHEMA,
            ),
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        if name == LINK_TO_PHOTOS:
            return self._photos(args)
        if name == LINK_TO_MEDIA_ITEM:
            return self._media_item(args)
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

    def _media_item(self, args: dict) -> ToolResult:
        title = _title(args) or "Photo"
        try:
            media_item_id = int(args.get("media_item_id") or 0)
        except (TypeError, ValueError):
            media_item_id = 0
        if self.session.get(MediaItem, media_item_id) is None:
            return self._result(LINK_TO_MEDIA_ITEM, title, None, f"No media item with id {media_item_id}.")
        return self._result(
            LINK_TO_MEDIA_ITEM, title, media_item_url(media_item_id),
            f"Link ready. It's shown under your answer as “{title}”; don't repeat the URL.", count=1)

    def _result(self, tool: str, title: str, url: Optional[str], text: str, count: int = 0) -> ToolResult:
        activity = ToolActivity(
            tool=tool, args={"title": title}, count=count, error=url is None,
            links=[AppLink(title=title, url=url)] if url else [],
        )
        return ToolResult(model_text=text, host_data=activity.to_dict())
