import pytest

from yaffo.site_agents.assistant.tool_providers.knowledge.knowledge import DocSection, KnowledgeBase
from yaffo.site_agents.assistant.tool_providers.knowledge.tools import READ_DOC, SEARCH_DOCS, KnowledgeToolProvider

pytestmark = pytest.mark.unit


def _section(anchor, heading, text):
    return DocSection(
        id=f"guide/faces.md#{anchor}", scope="guide", path="guide/faces.md", page_title="Faces",
        heading=heading, level=2 if anchor else 1, anchor=anchor,
        url=f"https://docs/guide/faces/#{anchor}" if anchor else "https://docs/guide/faces/", text=text,
    )


@pytest.fixture
def provider():
    return KnowledgeToolProvider(KnowledgeBase([
        _section("", "Faces", "Yaffo finds faces in photos."),
        _section("assign", "Assign a Cluster", "Pick the person, then assign the cluster."),
    ]))


def test_tools_are_declared(provider):
    assert [t.name for t in provider.get_tools()] == [SEARCH_DOCS, READ_DOC]


def test_search_returns_text_for_the_model_and_sources_for_the_ui(provider):
    result = provider.call_tool(SEARCH_DOCS, {"query": "assign cluster"})
    assert "[1] Faces › Assign a Cluster" in result.model_text
    assert "anchor: assign" in result.model_text
    assert result.host_data["tool"] == SEARCH_DOCS
    assert result.host_data["query"] == "assign cluster"
    assert result.host_data["count"] == 1
    assert result.host_data["sources"][0]["url"] == "https://docs/guide/faces/#assign"


def test_search_with_no_matches_and_an_unknown_scope(provider):
    result = provider.call_tool(SEARCH_DOCS, {"query": "zebra", "scope": "bogus"})
    assert "No documentation matched" in result.model_text
    assert result.host_data["count"] == 0


def test_read_whole_page_and_one_section(provider):
    page = provider.call_tool(READ_DOC, {"path": "guide/faces.md"})
    assert "Yaffo finds faces" in page.model_text and "Pick the person" in page.model_text
    assert page.host_data["title"] == "Faces"

    section = provider.call_tool(READ_DOC, {"path": "guide/faces.md", "anchor": "assign"})
    assert "Pick the person" in section.model_text
    assert "Yaffo finds faces" not in section.model_text
    assert section.host_data["title"] == "Faces › Assign a Cluster"


def test_read_missing_page_is_reported_not_raised(provider):
    result = provider.call_tool(READ_DOC, {"path": "nope.md"})
    assert "No page or section found" in result.model_text
    assert result.host_data["error"] is True
