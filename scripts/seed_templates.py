#!/usr/bin/env python3
"""Seed a 'Widget Templates' showcase page from the curated catalog.

Renders every template in yaffo/site_agents/widget_templates.py onto one page so
the consistent, app-styled designs can be reviewed in the browser. Same catalog the
agent's template tool serves, so what you see here is what the model is shown.

Run:
    python -m yaffo.scripts.seed_templates

Idempotent: re-running replaces the existing 'Widget Templates' page.
"""
from __future__ import annotations

from yaffo.app import create_app
from yaffo.db import db
from yaffo.db.repositories import custom_page_repository as page_repo
from yaffo.site_agents.widget_templates import TEMPLATES

_PAGE_TITLE = "Widget Templates"
_PAGE_SUBTITLE = "Reference designs for the AI page builder — consistent with the app's styling."


_COLUMNS = 12


def _laid_out() -> list[dict]:
    """Flow the templates across the 12-column grid in catalog order, wrapping to a
    new row when the next one doesn't fit — so half-width pairs (e.g. the pub/sub
    picker + spotlight) sit side by side."""
    items: list[dict] = []
    x = y = row_h = 0
    for template in TEMPLATES:
        if x + template.grid_w > _COLUMNS:
            x, y, row_h = 0, y + row_h, 0
        items.append(template.to_widget_item(x=x, y=y))
        x += template.grid_w
        row_h = max(row_h, template.grid_h)
    return items


def seed_templates() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()
        existing = {p.title: p.id for p in page_repo.list_pages(db.session)}
        if _PAGE_TITLE in existing:  # idempotent: replace the prior showcase
            page_repo.delete_page(db.session, existing[_PAGE_TITLE])
        page = page_repo.create_page(db.session, title=_PAGE_TITLE, subtitle=_PAGE_SUBTITLE)
        page_repo.save_page_widgets(db.session, page.id, _laid_out())
        print(f"Seeded '{page.title}' (id={page.id}) with {len(TEMPLATES)} template widgets")


if __name__ == "__main__":
    seed_templates()
