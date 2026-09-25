import pytest

from yaffo.site_agents.assistant.knowledge import DocSection, KnowledgeBase, knowledge_base, tokenize

pytestmark = pytest.mark.unit


def _section(path, anchor, heading, text, scope="guide"):
    return DocSection(
        id=f"{path}#{anchor}", scope=scope, path=path, page_title=path.split("/")[-1],
        heading=heading, level=2, anchor=anchor, url=f"https://docs/{path}#{anchor}", text=text,
    )


@pytest.fixture
def kb():
    return KnowledgeBase([
        _section("guide/faces.md", "assign", "Assign a Cluster", "Pick the person and assign the cluster of faces."),
        _section("guide/faces.md", "ignore", "Ignore Faces", "Ignore faces you don't want to name."),
        _section("guide/albums.md", "create", "Create an Album", "Albums collect photos you pick."),
        _section("development/faces.md", "impl", "Assignment internals",
                 "assign assign assign faces faces bulk link rows", scope="development"),
    ])


def test_tokenize_drops_stopwords_and_punctuation():
    assert tokenize("How do I assign the faces?") == ["assign", "faces"]


def test_search_ranks_heading_matches_and_prefers_the_guide(kb):
    hits = kb.search("assign faces")
    assert hits[0].section.anchor == "assign"
    assert {h.section.anchor for h in hits} >= {"assign", "impl"}


def test_search_scope_and_no_match(kb):
    assert [h.section.scope for h in kb.search("faces", scope="development")] == ["development"]
    assert kb.search("zebra") == []
    assert kb.search("the and of") == []


def test_snippet_is_centred_on_a_match():
    long_text = "filler " * 80 + "the thumbnail folder lives here " + "more " * 80
    kb = KnowledgeBase([_section("guide/x.md", "a", "X", long_text)])
    snippet = kb.search("thumbnail")[0].snippet
    assert "thumbnail" in snippet
    assert snippet.startswith("… ") and snippet.endswith(" …")


def test_page_and_section_lookup(kb):
    assert [s.anchor for s in kb.page("guide/faces.md")] == ["assign", "ignore"]
    assert kb.section("guide/faces.md", "ignore").heading == "Ignore Faces"
    assert kb.section("guide/faces.md", "nope") is None
    assert kb.page("missing.md") == []


def test_bundled_knowledge_loads_and_answers():
    bundled = knowledge_base()
    assert len(bundled.sections) > 100
    top = bundled.search("assign faces to a person")[0].section
    assert top.path == "guide/organize-review/assigning-faces.md"
