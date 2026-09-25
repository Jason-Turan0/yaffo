"""The assistant knowledge build: splitting docs into sections, MkDocs-compatible
anchors and URLs, and the freshness check that keeps the shipped bundle in step
with docs/."""
import json

import pytest

from scripts import build_assistant_knowledge as build

pytestmark = pytest.mark.unit


def test_shipped_bundle_matches_the_docs():
    """If this fails, docs/ changed without a rebuild. Run:
    python -m scripts.build_assistant_knowledge"""
    manifest = json.loads((build.OUTPUT_DIR / build.MANIFEST_FILE).read_text())
    assert manifest["source_hash"] == build.source_hash()
    assert manifest["format_version"] == build.FORMAT_VERSION
    lines = (build.OUTPUT_DIR / build.SECTIONS_FILE).read_text().splitlines()
    assert len(lines) == manifest["section_count"]


@pytest.mark.parametrize("heading, anchor", [
    ("Assign a Cluster", "assign-a-cluster"),
    ("`auto_assign_faces`", "auto_assign_faces"),
    ("Triggers: When an Automation Runs", "triggers-when-an-automation-runs"),
    ("Tier routing (`background_tasks/automation_dispatch.py`)",
     "tier-routing-background_tasksautomation_dispatchpy"),
    ("Café & Crème", "cafe-creme"),
])
def test_slugify_matches_mkdocs_heading_ids(heading, anchor):
    assert build.slugify(heading) == anchor


@pytest.mark.parametrize("rel_path, url", [
    ("index.md", "https://example.org/docs/"),
    ("development/index.md", "https://example.org/docs/development/"),
    ("guide/start-here/getting-started.md", "https://example.org/docs/guide/start-here/getting-started/"),
])
def test_page_url_uses_directory_urls(rel_path, url):
    assert build.page_url("https://example.org/docs", rel_path) == url


def test_split_page_ignores_headings_inside_code_fences():
    markdown = "# Title\nintro\n## Real\n```python\n# not a heading\n```\n### Sub\nbody\n"
    parts = build.split_page(markdown)
    assert [(level, heading) for level, heading, _ in parts] == [
        (0, ""), (1, "Title"), (2, "Real"), (3, "Sub"),
    ]
    assert "# not a heading" in "\n".join(parts[2][2])


def test_clean_text_drops_images_and_markup_but_keeps_link_text():
    text = build.clean_text(
        '!!! note "As of 2026"\n'
        "See [Indexing](indexing.md#top) ![shot](a.png){ loading=lazy }\n"
        "<kbd>Enter</kbd> works{: .class }\n"
    )
    assert text == "Note: As of 2026\nSee Indexing\nEnter works"


def test_build_sections_scopes_anchors_and_duplicate_headings(tmp_path):
    (tmp_path / "guide").mkdir()
    (tmp_path / "development").mkdir()
    (tmp_path / "index.md").write_text("# Home\nWelcome.\n")
    (tmp_path / "guide" / "faces.md").write_text(
        "# Faces\nLead.\n## Tips\nOne.\n## Tips\nTwo.\n## Empty\n### Child\nText.\n")
    (tmp_path / "development" / "notes.md").write_text("# Notes\n## `internal_thing`\nDetail.\n")

    sections = build.build_sections(tmp_path, "https://example.org/")

    by_id = {s.id: s for s in sections}
    assert by_id["guide/faces.md#tips"].text == "One."
    assert by_id["guide/faces.md#tips_1"].text == "Two."
    # A heading with no text of its own is skipped; its child keeps its anchor.
    assert "guide/faces.md#empty" not in by_id
    assert by_id["guide/faces.md#child"].url == "https://example.org/guide/faces/#child"
    assert by_id["guide/faces.md#"].heading == "Faces"
    notes = by_id["development/notes.md#internal_thing"]
    assert notes.scope == "development"
    assert notes.heading == "internal_thing"
    assert by_id["index.md#"].scope == "guide"


def test_excluded_docs_are_left_out(tmp_path, monkeypatch):
    (tmp_path / "development").mkdir()
    (tmp_path / "index.md").write_text("# Home\nx\n")
    (tmp_path / "development" / "plan.md").write_text("# Plan\nFuture.\n")
    monkeypatch.setattr(build, "EXCLUDED", frozenset({"development/plan.md"}))

    assert [p.name for p in build.source_files(tmp_path)] == ["index.md"]
