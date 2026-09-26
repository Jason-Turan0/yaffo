"""Build the assistant's page table from the Flask route table.

Writes `yaffo/assistant_knowledge/pages.json`: {endpoint: rule} for every page in
`yaffo/site_agents/assistant/app_pages.py` PAGES, read from `app.url_map`. The
assistant runs in the task worker, which has no Flask app, so it reads this file
instead. A test fails when it no longer matches the routes. Rebuild after adding,
removing or changing a page route:

    python -m scripts.build_assistant_pages
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from yaffo.app import create_app
from yaffo.site_agents.assistant.app_pages import PAGES, PAGES_FILE


def page_rules() -> dict[str, str]:
    """{endpoint: rule} for the PAGES endpoints, from a throwaway app's url_map."""
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(db_path=Path(tmp) / "routes.db", config={"TESTING": True})
        rules = {rule.endpoint: rule.rule for rule in app.url_map.iter_rules() if rule.endpoint in PAGES}
    return dict(sorted(rules.items()))


def render(rules: dict[str, str]) -> str:
    return json.dumps(rules, indent=2) + "\n"


def build(output: Path = PAGES_FILE) -> dict[str, str]:
    rules = page_rules()
    output.write_text(render(rules), encoding="utf-8")
    return rules


if __name__ == "__main__":
    print(f"Wrote {len(build())} pages to {PAGES_FILE}")
