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
class GenWidget:
    id: int
    title: str = "Untitled widget"
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
class GenMessage:
    role: str  # user | assistant
    content: str


@dataclass
class GenPage:
    id: int
    title: str
    theme_prompt: str = ""
    show_title: bool = True
    widgets: list[GenWidget] = field(default_factory=list)
    messages: list[GenMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


_pages: dict[int, GenPage] = {}
_page_ids = count(1)
_widget_ids = count(1)


# Stand-in for AI-generated widget content. A real widget's html/css/js will come
# from Claude; this canned content renders the injected window.__DATA__ so the
# sandbox + data-injection loop can be exercised before any AI is wired in.
_STUB_DATA_QUERY = {"location": "Maine", "limit": 6}
_STUB_CSS = (
    "body{font-family:-apple-system,sans-serif;margin:0;padding:8px;color:#495057}"
    ".tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(72px,1fr));gap:6px}"
    ".tile{background:#ced4da;border-radius:4px;aspect-ratio:1;display:flex;"
    "align-items:flex-end;padding:4px;font-size:10px;line-height:1.2}"
)
_STUB_HTML = "<div class='tiles' id='tiles'></div>"
_STUB_JS = (
    "const c=document.getElementById('tiles');"
    "(window.__DATA__.photos||[]).forEach(p=>{"
    "const d=document.createElement('div');d.className='tile';"
    "d.textContent=p.location+' #'+p.id;c.appendChild(d);});"
)


def resolve_data(data_query: dict) -> dict:
    """Stub for the server-side query resolver: returns fake photos in the
    shape promised to widget code (see docs/ai-page-builder.md)."""
    limit = int(data_query.get("limit", 6))
    location = data_query.get("location", "Somewhere")
    photos = [
        {
            "id": i + 1,
            "url": "",
            "thumb_url": "",
            "taken_at": f"2023-07-{(i % 28) + 1:02d}",
            "location": location,
            "persons": ["Mom", "Dad"][: (i % 2) + 1],
            "tags": ["beach"],
            "width": 800,
            "height": 600,
        }
        for i in range(limit)
    ]
    return {"photos": photos}


def list_pages() -> list[GenPage]:
    return sorted(_pages.values(), key=lambda p: p.updated_at, reverse=True)


def get_page(page_id: int) -> Optional[GenPage]:
    return _pages.get(page_id)


def create_page(title: str, theme_prompt: str = "") -> GenPage:
    page = GenPage(id=next(_page_ids), title=title, theme_prompt=theme_prompt)
    _pages[page.id] = page
    return page


def _new_widget(page: GenPage, title: str, prompt: str = "") -> GenWidget:
    next_y = max((w.grid_y + w.grid_h for w in page.widgets), default=0)
    widget = GenWidget(
        id=next(_widget_ids),
        title=title,
        prompt=prompt,
        data_query=dict(_STUB_DATA_QUERY),
        html=_STUB_HTML,
        css=_STUB_CSS,
        js=_STUB_JS,
        status="ready",
        grid_x=0,
        grid_y=next_y,
        grid_w=4,
        grid_h=3,
    )
    page.widgets.append(widget)
    page.updated_at = datetime.now()
    return widget


def add_widget(page_id: int) -> Optional[GenWidget]:
    page = _pages.get(page_id)
    if page is None:
        return None
    return _new_widget(page, title="Untitled widget")


def remove_widget(page_id: int, widget_id: int) -> None:
    page = _pages.get(page_id)
    if page is None:
        return
    page.widgets = [w for w in page.widgets if w.id != widget_id]
    page.updated_at = datetime.now()


def update_layout(page_id: int, layout: list[dict]) -> None:
    page = _pages.get(page_id)
    if page is None:
        return
    by_id = {w.id: w for w in page.widgets}
    for item in layout:
        widget = by_id.get(int(item["id"]))
        if widget is None:
            continue
        widget.grid_x = int(item["x"])
        widget.grid_y = int(item["y"])
        widget.grid_w = int(item["w"])
        widget.grid_h = int(item["h"])
        if item.get("title"):
            widget.title = item["title"]
    page.updated_at = datetime.now()


def add_message(page_id: int, role: str, content: str) -> Optional[GenMessage]:
    page = _pages.get(page_id)
    if page is None:
        return None
    message = GenMessage(role=role, content=content)
    page.messages.append(message)
    page.updated_at = datetime.now()
    return message


def generate_widget(page_id: int, prompt: str) -> Optional[GenWidget]:
    """Mock model generation: turns a user prompt into a stub widget. Stands in
    for a Claude call that would emit the widget's data_query + html/css/js."""
    page = _pages.get(page_id)
    if page is None:
        return None
    return _new_widget(page, title=_title_from_prompt(prompt), prompt=prompt)


def _title_from_prompt(prompt: str) -> str:
    words = prompt.strip().split()
    title = " ".join(words[:5])
    if len(words) > 5:
        title += "…"
    return title[:1].upper() + title[1:] if title else "Untitled widget"


def update_page(
    page_id: int,
    title: Optional[str] = None,
    theme_prompt: Optional[str] = None,
    show_title: Optional[bool] = None,
) -> Optional[GenPage]:
    page = _pages.get(page_id)
    if page is None:
        return None
    if title is not None:
        page.title = title
    if theme_prompt is not None:
        page.theme_prompt = theme_prompt
    if show_title is not None:
        page.show_title = show_title
    page.updated_at = datetime.now()
    return page


def delete_page(page_id: int) -> None:
    _pages.pop(page_id, None)