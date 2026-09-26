"""The app's pages the assistant can link to.

The URL rules come from the Flask route table: scripts/build_assistant_pages.py
reads `app.url_map` and writes `yaffo/assistant_knowledge/pages.json` (the rule
and its parameters for every page below). It's generated rather than read at
run time because the assistant runs in the task worker, which has no Flask app.
A test rebuilds it and fails when it no longer matches the routes.

What *is* a page can't be read off a rule (`/jobs/section` is a fragment,
`/faces/<int:face_id>` an image), so every GET route is classified here, by
endpoint: PAGES with a one-line description the model chooses from, or
NOT_PAGES. A test fails when a GET route is in neither, so a new route is
classified when it's added.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Optional

from werkzeug.routing import BuildError, Map, Rule

from yaffo.site_agents.assistant.knowledge import KNOWLEDGE_DIR

PAGES_FILE = KNOWLEDGE_DIR / "pages.json"

PAGES: dict[str, str] = {
    "index": "The photo library, unfiltered (use link_to_photos to filter it).",
    "albums_index": "All albums.",
    "albums_show": "One album's photos.",
    "albums_add": "Pick photos to add to an album.",
    "assistant_index": "Ask Yaffo full page, with every conversation.",
    "faces_index": "Assign faces: unassigned faces grouped for naming.",
    "people_list": "All people, with how many faces each has.",
    "person_faces": "The faces assigned to one person, to review or remove.",
    "locations_list": "The map of where photos were taken; also where location names are set.",
    "media_view": "One photo or video with its details, faces, tags and location.",
    "pages_detail": "A custom page the user built.",
    "pages_design": "The designer for a custom page (the page builder chat).",
    "settings_index": "Settings: media folders, thumbnails, language, units, AI generation, assistant, labels, system information.",
    "sharing_index": "Sharing: paired devices and what's shared with them.",
    "sharing_device": "One paired device: what it shares with you and what you share with it.",
    "sharing_settings": "Sharing settings for this device.",
    "themes_index": "Themes: choose or build a theme.",
    "themes_show": "One theme, with its preview and build chat.",
    "utilities_index": "Utilities (opens Index Photos).",
    "utilities_index_photos": "Index Photos: scan the media folders and follow indexing jobs.",
    "utilities_remove_duplicates": "Remove Duplicates: find duplicate photos.",
    "utilities_remove_duplicates_results": "The results of one duplicate scan, by its job id.",
    "automations_index": "Automations: every system and custom automation.",
    "automations_show": "One automation: its settings, triggers and run history.",
    "automations_edit": "The builder chat and code for a custom automation.",
    "automations_edit_triggers": "When an automation runs: its schedule and events.",
}

# GET routes that aren't pages: JSON APIs, htmx fragments and polls, files and
# images, stylesheets, and pages that need context the assistant can't give.
NOT_PAGES: frozenset[str] = frozenset({
    "assistant_conversations", "assistant_conversation", "fs_list", "location_autocomplete",
    "get_thumbnail_stats_api", "get_tag_values", "settings_thumbnail_stats_stream",
    "face_thumbnail", "favicon", "placeholder", "media", "media_poster", "media_by_path",
    "job_fragment", "job_status", "jobs_section",
    "pages_version_status", "pages_widget_frame",
    "sharing_sidebar", "sharing_sidebar_shared_with_me", "sharing_settings_section",
    "sharing_device_preview", "sharing_device_tag_values", "sharing_device_transfers",
    # The remote gallery needs a scope (a shared folder or album) from the device page.
    "sharing_device_files",
    "themes_status", "theme_css", "theme_preview_css", "theme_tokens_css",
    "automations_status", "automations_runs", "automations_validate_cron",
    "utilities_index_photos_scan",
})

_ARGUMENT_RE = re.compile(r"<(?:(\w+)(?:\([^)]*\))?:)?(\w+)>")


def rule_arguments(rule: str) -> dict[str, str]:
    """A rule's parameters and their converters, e.g. {"person_id": "int"}."""
    return {name: converter or "string" for converter, name in _ARGUMENT_RE.findall(rule)}


@dataclass(frozen=True)
class AppPage:
    endpoint: str
    rule: str
    arguments: dict[str, str]
    description: str


@lru_cache(maxsize=1)
def app_pages() -> dict[str, AppPage]:
    """The linkable pages by endpoint: rules from pages.json, descriptions from PAGES."""
    rules = json.loads(PAGES_FILE.read_text(encoding="utf-8"))
    return {
        endpoint: AppPage(endpoint, rules[endpoint], rule_arguments(rules[endpoint]), description)
        for endpoint, description in PAGES.items()
        if endpoint in rules
    }


@lru_cache(maxsize=1)
def _url_map() -> Map:
    return Map([Rule(page.rule, endpoint=page.endpoint) for page in app_pages().values()])


def page_url(endpoint: str, values: Optional[Mapping[str, Any]] = None) -> str:
    """The app-relative URL of a page. Values that aren't the rule's parameters
    become the query string (lists repeat the parameter). Raises ValueError for an
    unknown page or missing parameters."""
    if endpoint not in app_pages():
        raise ValueError(f"unknown page {endpoint!r}")
    try:
        return _url_map().bind("").build(endpoint, dict(values or {}))
    except BuildError as exc:
        raise ValueError(f"can't build {endpoint!r} with {sorted((values or {}).keys())}") from exc
