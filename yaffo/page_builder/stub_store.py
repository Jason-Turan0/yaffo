"""In-memory stub store for the page builder.

This mirrors the proposed schema in docs/ai-page-builder.md so the UI can be
built and the schema validated before committing to SQLAlchemy models. State is
process-local and resets on restart.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from itertools import count
from typing import Optional


@dataclass
class GenBlock:
    id: int
    prompt: str = ""
    data_query: dict = field(default_factory=dict)
    html: str = ""
    css: str = ""
    js: str = ""
    status: str = "empty"  # empty | generating | ready | error
    grid_x: int = 0
    grid_y: int = 0
    grid_w: int = 4
    grid_h: int = 3


@dataclass
class GenPage:
    id: int
    title: str
    theme_prompt: str = ""
    blocks: list[GenBlock] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


_pages: dict[int, GenPage] = {}
_page_ids = count(1)
_block_ids = count(1)


def list_pages() -> list[GenPage]:
    return sorted(_pages.values(), key=lambda p: p.updated_at, reverse=True)


def get_page(page_id: int) -> Optional[GenPage]:
    return _pages.get(page_id)


def create_page(title: str, theme_prompt: str = "") -> GenPage:
    page = GenPage(id=next(_page_ids), title=title, theme_prompt=theme_prompt)
    _pages[page.id] = page
    return page


def add_block(page_id: int) -> Optional[GenBlock]:
    page = _pages.get(page_id)
    if page is None:
        return None
    next_y = max((b.grid_y + b.grid_h for b in page.blocks), default=0)
    block = GenBlock(id=next(_block_ids), grid_x=0, grid_y=next_y, grid_w=4, grid_h=3)
    page.blocks.append(block)
    page.updated_at = datetime.now()
    return block


def remove_block(page_id: int, block_id: int) -> None:
    page = _pages.get(page_id)
    if page is None:
        return
    page.blocks = [b for b in page.blocks if b.id != block_id]
    page.updated_at = datetime.now()


def update_layout(page_id: int, layout: list[dict]) -> None:
    page = _pages.get(page_id)
    if page is None:
        return
    by_id = {b.id: b for b in page.blocks}
    for item in layout:
        block = by_id.get(int(item["id"]))
        if block is None:
            continue
        block.grid_x = int(item["x"])
        block.grid_y = int(item["y"])
        block.grid_w = int(item["w"])
        block.grid_h = int(item["h"])
    page.updated_at = datetime.now()


def update_page(page_id: int, title: Optional[str] = None, theme_prompt: Optional[str] = None) -> Optional[GenPage]:
    page = _pages.get(page_id)
    if page is None:
        return None
    if title is not None:
        page.title = title
    if theme_prompt is not None:
        page.theme_prompt = theme_prompt
    page.updated_at = datetime.now()
    return page


def delete_page(page_id: int) -> None:
    _pages.pop(page_id, None)