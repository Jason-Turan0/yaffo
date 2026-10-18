"""Markdown listing a documentation healing run's artifacts, grouped by whether each
page had changes.

Usage:
  docs_artifact_summary.py --evidence DIR --artifacts FILE --artifact-url BASE [--run-url URL]

  --evidence      directory holding one downloaded docs-heal-<id> artifact per page,
                  each with triage.json and captures/report.json
  --artifacts     the run's artifact listing, as returned by the GitHub REST API
  --artifact-url  link prefix for one artifact, completed by its numeric id
  --run-url       the workflow run, linked when an artifact listing is unavailable

Pages with changes come first, with the shots that changed, what triage decided,
and links to their capture, healing, and patch artifacts. The capture artifact holds
each changed shot's review sheet, <shot>.review.webp. Pages with no changes follow,
collapsed, since there is nothing to review there.

Standard library only, so the workflow can run it before any setup step.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

PREFIXES = ("docs-capture-", "docs-heal-", "docs-patch-")
ACTION_STATUSES = {"new", "changed", "unstable"}
MARK = {
    "intended_change": "✅",
    "walkthrough_defect": "🔧",
    "application_regression": "🐛",
    "environment_instability": "🌫️",
    "inconclusive": "❓",
}


@dataclass
class Page:
    id: str
    name: str
    shots: list[dict] = field(default_factory=list)
    verdicts: list[dict] = field(default_factory=list)
    error: str | None = None
    evidence: bool = False

    @property
    def changed_shots(self) -> list[dict]:
        return [shot for shot in self.shots if shot.get("status") in ACTION_STATUSES]

    @property
    def has_changes(self) -> bool:
        # Without evidence nothing says the page is clean, so it is listed for review.
        return bool(self.changed_shots or self.verdicts or self.error or not self.evidence)


def page_id(page: str) -> str:
    """The matrix id a page's artifacts are named by: area/page-name -> area__page__name."""
    return page.replace("/", "__").replace("-", "__")


def load_pages(evidence: Path) -> dict[str, Page]:
    pages: dict[str, Page] = {}
    for directory in sorted(path for path in evidence.glob("docs-heal-*") if path.is_dir()):
        id_ = directory.name.removeprefix("docs-heal-")
        page = Page(id=id_, name=id_, evidence=True)
        report = directory / "captures" / "report.json"
        if report.exists():
            for result in json.loads(report.read_text()).get("results", []):
                page.name = result.get("page", page.name)
                page.shots += result.get("shots", [])
                page.error = page.error or result.get("error")
        triage = directory / "triage.json"
        if triage.exists():
            verdicts = json.loads(triage.read_text()).get("verdicts", [])
            page.verdicts = verdicts
            if verdicts and page.name == id_:
                page.name = verdicts[0].get("page", page.name)
        pages[id_] = page
    return pages


def latest_artifacts(listing: dict) -> dict[str, dict]:
    """The newest unexpired artifact per name: re-runs upload under the same name."""
    latest: dict[str, dict] = {}
    for artifact in listing.get("artifacts", []):
        name = artifact.get("name", "")
        if artifact.get("expired") or not name.startswith(PREFIXES):
            continue
        if name not in latest or artifact.get("created_at", "") > latest[name].get("created_at", ""):
            latest[name] = artifact
    return latest


def shot_label(shot: dict) -> str:
    name = shot.get("target", "").rsplit("/", 1)[-1]
    status = shot.get("status")
    if status == "unstable":
        detail = "did not reproduce"
    elif status == "new":
        detail = "new"
    else:
        detail = (shot.get("diff") or {}).get("change") or status
    return f"`{name}` {detail}"


def verdict_shots(page: Page) -> str:
    """Shots named by triage, for a page whose report no longer marks them: the
    healing evidence is recaptured after promotion, when they match again."""
    names = []
    for verdict in page.verdicts:
        target = verdict.get("target", "")
        name = target.rsplit("/", 1)[-1]
        if target.endswith(".webp") and f"`{name}`" not in names:
            names.append(f"`{name}`")
    return "<br>".join(names) or "—"


def outcome(page: Page) -> str:
    if page.error and not page.verdicts:
        return "walkthrough failed"
    if not page.evidence:
        return "no healing evidence"
    if not page.verdicts:
        return "—"
    parts = []
    for verdict in page.verdicts:
        triage = verdict.get("triage", {})
        classification = triage.get("classification", "")
        action = triage.get("recommendedAction", "")
        resolved = " (resolved)" if verdict.get("resolved") else ""
        parts.append(f"{MARK.get(classification, '•')} {classification} → {action}{resolved}")
    # One page can carry several verdicts; repeat identical ones once with a count.
    counted: dict[str, int] = {}
    for part in parts:
        counted[part] = counted.get(part, 0) + 1
    return "<br>".join(f"{part} ×{count}" if count > 1 else part for part, count in counted.items())


def links(page: Page, artifacts: dict[str, dict], artifact_url: str) -> str:
    found = []
    for prefix, label in (("docs-capture-", "capture"), ("docs-heal-", "heal"), ("docs-patch-", "patch")):
        artifact = artifacts.get(prefix + page.id)
        if artifact:
            found.append(f"[{label}]({artifact_url}{artifact['id']})")
    return " · ".join(found) or "—"


def render(pages: dict[str, Page], artifacts: dict[str, dict], artifact_url: str, run_url: str | None) -> str:
    # Pages whose artifacts were uploaded but whose healing evidence was not downloaded.
    for name in artifacts:
        for prefix in PREFIXES:
            if name.startswith(prefix):
                id_ = name.removeprefix(prefix)
                pages.setdefault(id_, Page(id=id_, name=id_))

    changed = sorted((page for page in pages.values() if page.has_changes), key=lambda page: page.name)
    unchanged = sorted((page for page in pages.values() if not page.has_changes), key=lambda page: page.name)
    lines = ["## Artifacts", ""]
    if not pages:
        target = f"[the workflow run]({run_url})" if run_url else "the workflow run"
        return "\n".join(lines + [f"No per-page artifacts were found; see {target}.", ""])

    lines += [f"### Pages with changes ({len(changed)})", ""]
    if changed:
        lines += [
            "Each changed shot's review sheet is `<shot>.review.webp` inside the page's capture "
            "artifact; a shot that did not reproduce also has `<shot>.repeat.review.webp`.",
            "",
            "| Page | Changed shots | Outcome | Artifacts |",
            "| --- | --- | --- | --- |",
        ]
        for page in changed:
            shots = "<br>".join(shot_label(shot) for shot in page.changed_shots) or verdict_shots(page)
            lines.append(f"| `{page.name}` | {shots} | {outcome(page)} | {links(page, artifacts, artifact_url)} |")
    else:
        lines.append("_None: every captured shot matched what is committed._")
    lines.append("")

    if unchanged:
        lines += [f"<details><summary>Pages with no changes ({len(unchanged)})</summary>", ""]
        lines += [f"- `{page.name}` — {links(page, artifacts, artifact_url)}" for page in unchanged]
        lines += ["", "</details>", ""]
    lines.append("_Artifacts expire according to their configured retention period._")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--artifact-url", required=True)
    parser.add_argument("--run-url", default=None)
    args = parser.parse_args(argv)
    pages = load_pages(args.evidence) if args.evidence.is_dir() else {}
    try:
        listing = json.loads(args.artifacts.read_text())
    except (OSError, ValueError):
        listing = {}
    sys.stdout.write(render(pages, latest_artifacts(listing), args.artifact_url, args.run_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
