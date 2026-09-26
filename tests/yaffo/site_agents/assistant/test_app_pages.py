"""The assistant's page table: built from the Flask routes, and every GET route
classified as a page or not."""
import json

import pytest

from scripts.build_assistant_pages import page_rules, render
from yaffo.site_agents.assistant.app_pages import NOT_PAGES, PAGES, PAGES_FILE, page_url, rule_arguments

pytestmark = pytest.mark.unit


def test_shipped_page_table_matches_the_routes():
    """Regenerate with: python -m scripts.build_assistant_pages"""
    assert PAGES_FILE.read_text(encoding="utf-8") == render(page_rules())


def test_every_get_route_is_classified(app):
    get_endpoints = {
        rule.endpoint for rule in app.url_map.iter_rules()
        if "GET" in rule.methods and rule.endpoint != "static"
    }
    unclassified = get_endpoints - set(PAGES) - NOT_PAGES
    assert unclassified == set(), "add these to PAGES or NOT_PAGES in app_pages.py"
    assert set(PAGES) & NOT_PAGES == set()
    # Nothing listed that no longer exists.
    assert (set(PAGES) | NOT_PAGES) - get_endpoints == set()


def test_every_page_is_in_the_table():
    assert set(json.loads(PAGES_FILE.read_text(encoding="utf-8"))) == set(PAGES)


def test_rule_arguments():
    assert rule_arguments("/people/<int:person_id>/faces") == {"person_id": "int"}
    assert rule_arguments("/utilities/automations/<slug>/edit") == {"slug": "string"}
    assert rule_arguments("/settings") == {}


def test_page_url_builds_from_the_rules():
    assert page_url("person_faces", {"person_id": 10}) == "/people/10/faces"
    assert page_url("index", {"person": [1, 2], "year": 2019}) == "/?person=1&person=2&year=2019"
    with pytest.raises(ValueError, match="unknown page"):
        page_url("api_whatever")
    with pytest.raises(ValueError, match="can't build"):
        page_url("person_faces", {})
