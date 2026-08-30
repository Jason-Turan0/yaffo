from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _percentage(covered: int, total: int) -> str:
    if total == 0:
        return "100.00%"
    return f"{covered / total * 100:.2f}%"


def _table(title: str, rows: list[tuple[str, int, int]], artifact_url: str | None) -> str:
    lines = [
        f"### {title}",
        "",
        "| Metric | Covered | Total | Coverage |",
        "| --- | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {name} | {covered} | {total} | {_percentage(covered, total)} |"
        for name, covered, total in rows
    )
    if artifact_url:
        lines.extend(["", f"[Download the full HTML coverage report]({artifact_url})"])
    return "\n".join(lines) + "\n"


def python_summary(report: dict[str, Any], artifact_url: str | None = None) -> str:
    totals = report["totals"]
    return _table(
        "Python coverage",
        [
            ("Lines", totals["covered_lines"], totals["num_statements"]),
            ("Branches", totals["covered_branches"], totals["num_branches"]),
        ],
        artifact_url,
    )


def javascript_summary(report: dict[str, Any], artifact_url: str | None = None) -> str:
    totals = report["total"]
    rows = [
        (name.title(), totals[name]["covered"], totals[name]["total"])
        for name in ("lines", "statements", "functions", "branches")
    ]
    return _table("App JavaScript coverage", rows, artifact_url)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a coverage JSON file as a GitHub job summary.")
    parser.add_argument("kind", choices=("python", "javascript"))
    parser.add_argument("report", type=Path)
    parser.add_argument("--artifact-url")
    args = parser.parse_args()

    with args.report.open(encoding="utf-8") as report_file:
        report = json.load(report_file)

    renderer = python_summary if args.kind == "python" else javascript_summary
    print(renderer(report, args.artifact_url), end="")


if __name__ == "__main__":
    main()
