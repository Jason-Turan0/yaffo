"""The grouped artifact listing on documentation healing PRs and run summaries."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "yaffo_ui_tests" / "scripts" / "docs_artifact_summary.py"
BASE = "https://github.example/runs/1/artifacts/"


def _load():
    spec = importlib.util.spec_from_file_location("docs_artifact_summary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["docs_artifact_summary"] = module
    spec.loader.exec_module(module)
    return module


summary = _load()


def _evidence(root: Path, page: str, shots: list[dict], verdicts: list[dict] | None = None, error=None):
    directory = root / f"docs-heal-{summary.page_id(page)}"
    (directory / "captures").mkdir(parents=True)
    result = {"page": page, "shots": shots}
    if error:
        result["error"] = error
    (directory / "captures" / "report.json").write_text(json.dumps({"results": [result]}))
    if verdicts is not None:
        (directory / "triage.json").write_text(json.dumps({"verdicts": verdicts}))


def _artifacts(*names: str, expired: tuple[str, ...] = ()) -> dict:
    return {"artifacts": [
        {"id": index + 10, "name": name, "expired": name in expired, "created_at": "2026-10-17T00:00:00Z"}
        for index, name in enumerate(names)
    ]}


def _run(tmp_path: Path, listing: dict) -> str:
    listing_file = tmp_path / "artifacts.json"
    listing_file.write_text(json.dumps(listing))
    pages = summary.load_pages(tmp_path / "evidence")
    return summary.render(pages, summary.latest_artifacts(listing), BASE, None)


def test_pages_are_grouped_by_whether_they_changed(tmp_path):
    evidence = tmp_path / "evidence"
    _evidence(evidence, "start-here/ask-yaffo", [
        {"target": "start-here/assets/ask-yaffo/ask-yaffo-context.webp", "status": "changed",
         "diff": {"change": "layout-only"}},
        {"target": "start-here/assets/ask-yaffo/ask-yaffo-change-card.webp", "status": "unchanged"},
    ], [{"page": "start-here/ask-yaffo", "target": "x",
         "triage": {"classification": "intended_change", "recommendedAction": "promote"}}])
    _evidence(evidence, "library-basics/indexing-library", [
        {"target": "library-basics/assets/indexing-library/library-health.webp", "status": "unchanged"},
    ], [])
    out = _run(tmp_path, _artifacts(
        "docs-capture-start__here__ask__yaffo", "docs-heal-start__here__ask__yaffo",
        "docs-patch-start__here__ask__yaffo",
        "docs-capture-library__basics__indexing__library", "docs-heal-library__basics__indexing__library"))

    changed, unchanged = out.split("<details>")
    assert "### Pages with changes (1)" in changed
    assert "`start-here/ask-yaffo`" in changed
    assert "`ask-yaffo-context.webp` layout-only" in changed
    assert "change-card" not in changed, "unchanged shots of a changed page are not listed"
    assert "✅ intended_change → promote" in changed
    assert f"[capture]({BASE}10) · [heal]({BASE}11) · [patch]({BASE}12)" in changed
    assert "Pages with no changes (1)" in unchanged
    assert "`library-basics/indexing-library`" in unchanged


def test_unstable_new_and_failed_pages_count_as_changes(tmp_path):
    evidence = tmp_path / "evidence"
    _evidence(evidence, "create-customize/custom-pages", [
        {"target": "create-customize/assets/custom-pages/custom-page-design.webp", "status": "unstable"}])
    _evidence(evidence, "create-customize/themes", [
        {"target": "create-customize/assets/themes/new-shot.webp", "status": "new"}])
    _evidence(evidence, "organize-review/labels", [], error="locator.waitFor timed out")
    out = _run(tmp_path, _artifacts())
    assert "### Pages with changes (3)" in out
    assert "`custom-page-design.webp` did not reproduce" in out
    assert "`new-shot.webp` new" in out
    assert "walkthrough failed" in out
    assert "<details>" not in out


def test_a_page_without_healing_evidence_is_listed_for_review(tmp_path):
    (tmp_path / "evidence").mkdir()
    out = _run(tmp_path, _artifacts("docs-capture-organize__review__locations"))
    assert "### Pages with changes (1)" in out
    assert "no healing evidence" in out


def test_repeated_verdicts_are_counted_once(tmp_path):
    verdict = {"page": "p/q", "triage": {"classification": "environment_instability",
                                         "recommendedAction": "quarantine"}}
    _evidence(tmp_path / "evidence", "p/q",
              [{"target": "p/assets/q/a.webp", "status": "changed"}, {"target": "p/assets/q/b.webp", "status": "changed"}],
              [verdict, verdict])
    out = _run(tmp_path, _artifacts())
    assert "🌫️ environment_instability → quarantine ×2" in out


def test_expired_and_superseded_artifacts_are_not_linked(tmp_path):
    _evidence(tmp_path / "evidence", "p/q", [{"target": "p/assets/q/a.webp", "status": "changed"}], [])
    listing = {"artifacts": [
        {"id": 1, "name": "docs-capture-p__q", "expired": False, "created_at": "2026-10-16T00:00:00Z"},
        {"id": 2, "name": "docs-capture-p__q", "expired": False, "created_at": "2026-10-17T00:00:00Z"},
        {"id": 3, "name": "docs-heal-p__q", "expired": True, "created_at": "2026-10-17T00:00:00Z"},
    ]}
    out = _run(tmp_path, listing)
    assert f"[capture]({BASE}2)" in out
    assert f"({BASE}1)" not in out and "[heal]" not in out


def test_the_cli_survives_a_missing_listing(tmp_path, capsys):
    assert summary.main(["--evidence", str(tmp_path / "none"), "--artifacts", str(tmp_path / "none.json"),
                         "--artifact-url", BASE, "--run-url", "https://github.example/runs/1"]) == 0
    assert "[the workflow run](https://github.example/runs/1)" in capsys.readouterr().out


def test_shots_named_by_triage_are_listed_when_the_report_has_moved_on(tmp_path):
    """Healing evidence is recaptured after promotion, so its report can say unchanged."""
    _evidence(tmp_path / "evidence", "reference-maintenance/settings",
              [{"target": "reference-maintenance/assets/settings/settings-overview.webp", "status": "unchanged"}],
              [{"page": "reference-maintenance/settings",
                "target": "reference-maintenance/assets/settings/settings-overview.webp",
                "triage": {"classification": "intended_change", "recommendedAction": "promote"}}])
    out = _run(tmp_path, _artifacts())
    assert "| `reference-maintenance/settings` | `settings-overview.webp` |" in out
