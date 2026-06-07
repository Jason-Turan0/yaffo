"""In-memory stub store for the page builder.

This mirrors the proposed schema in docs/ai-page-builder.md so the UI can be
built and the schema validated before committing to SQLAlchemy models. State is
process-local and resets on restart.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from itertools import count
from typing import Optional


@dataclass
class GenWidget:
    id: str
    title: str = "Untitled widget"
    prompt: str = ""
    data_query: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)  # widget-owned persisted state
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


def new_widget_id() -> str:
    """A fresh widget id. Widgets are identified by GUID so the client can mint
    ids for drafts and they stay stable from creation through Save."""
    return uuid.uuid4().hex

# Reference data available in the dev library; stub data is drawn from these.
_PHOTO_IDS = list(range(1, 16))
_LOCATIONS = ["Acadia NP", "Portland, ME", "Bar Harbor", "Camden"]
_TAGS = ["beach", "hike", "sunset", "family", "boat"]
# (name, face photo id) — face thumbnails reference real photos in the dev library.
_PEOPLE_DATA = [("Michelle", 2), ("Malia", 3), ("Obama", 4)]
_PEOPLE = [name for name, _ in _PEOPLE_DATA]
_YEARS = [2021, 2022, 2023]


# ---------------------------------------------------------------------------
# Stub data resolvers
#
# data_query is a dict of named queries: {query_name: {"source": ..., ...filters}}.
# Each named query is resolved independently against its source and returned
# under the same name, so widget code reads window.__DATA__[query_name] and
# stitches the named results together. Filterable widgets request a broad photo
# set plus "facets" and filter client-side (the sandbox blocks live fetches, so
# all candidate data is pre-injected).
# ---------------------------------------------------------------------------

def _fake_photos(data_query: dict) -> list[dict]:
    limit = int(data_query.get("limit", 9))
    fixed_location = data_query.get("location")
    person = data_query.get("person")
    ids = random.sample(_PHOTO_IDS, min(limit, len(_PHOTO_IDS)))
    photos = []
    for pid in ids:
        year = _YEARS[pid % len(_YEARS)]
        persons = [_PEOPLE[pid % len(_PEOPLE)], _PEOPLE[(pid + 1) % len(_PEOPLE)]]
        if person:  # stubbed server-side filter: pretend these match the person
            persons = [person] + [p for p in persons if p != person]
        photos.append(
            {
                "id": pid,
                "url": f"/photos/{pid}",
                "thumb_url": f"/photos/{pid}",
                "taken_at": f"{year}-{(pid % 12) + 1:02d}-{(pid % 27) + 1:02d}",
                "year": year,
                "location": fixed_location or _LOCATIONS[pid % len(_LOCATIONS)],
                "persons": persons,
                "tags": [_TAGS[pid % len(_TAGS)], _TAGS[(pid + 2) % len(_TAGS)]],
                "width": 800,
                "height": 600,
            }
        )
    return photos


def _fake_locations() -> list[dict]:
    counts = [67, 42, 28, 15]
    coords = [(44.34, -68.27), (43.66, -70.26), (44.39, -68.20), (44.21, -69.06)]
    return [
        {"name": name, "lat": lat, "lon": lon, "count": count}
        for name, (lat, lon), count in zip(_LOCATIONS, coords, counts)
    ]


def _fake_people(limit: int) -> list[dict]:
    return [
        {"name": name, "photo_count": 120 - i * 17, "face_thumb_url": f"/photos/{photo_id}"}
        for i, (name, photo_id) in enumerate(_PEOPLE_DATA[:limit])
    ]


def _fake_tags() -> list[dict]:
    return [{"name": tag, "count": 90 - i * 12} for i, tag in enumerate(_TAGS)]


def _fake_stats() -> dict:
    return {"photos": 1284, "people": 12, "locations": 8, "years": "2021–2023"}


def _fake_facets() -> dict:
    return {"years": _YEARS, "tags": _TAGS, "locations": _LOCATIONS, "persons": _PEOPLE}


_SOURCE_RESOLVERS = {
    "photos": lambda q: _fake_photos(q),
    "locations": lambda q: _fake_locations(),
    "persons": lambda q: _fake_people(int(q.get("limit", 6))),
    "tags": lambda q: _fake_tags(),
    "stats": lambda q: _fake_stats(),
    "facets": lambda q: _fake_facets(),
}


def resolve_data(data_query: dict) -> dict:
    """Stub for the server-side query resolver. data_query is a dict of named
    queries ({query_name: {"source": ..., ...filters}}); each is resolved
    independently against its source and returned under the same name, so widget
    code can stitch the named results together (see docs/ai-page-builder.md)."""
    result = {}
    for name, query in data_query.items():
        resolver = _SOURCE_RESOLVERS.get(query.get("source"))
        if resolver is not None:
            result[name] = resolver(query)
    return result


def resolve_query(query: dict):
    """Resolve a single query spec ({"source": ..., ...filters}). Used for live,
    server-side queries a widget runs after load (via the parent broker)."""
    resolver = _SOURCE_RESOLVERS.get(query.get("source"))
    return resolver(query) if resolver is not None else None


def set_widget_state(page_id: int, widget_id: str, state: dict) -> None:
    """Persist a widget's own state blob (e.g. its current filter selection).
    Injected back into the widget as window.__STATE__ on the next render."""
    page = _pages.get(page_id)
    if page is None:
        return
    for widget in page.widgets:
        if widget.id == widget_id:
            widget.state = state or {}
            page.updated_at = datetime.now()
            return


# ---------------------------------------------------------------------------
# Stub widget catalog
#
# Each entry stands in for what Claude would emit: a `data_query` (filter +
# sources) plus free-form html/css/js that renders the injected window.__DATA__.
# ---------------------------------------------------------------------------

_PHOTO_GRID = {
    "title": "Photo grid",
    "grid_w": 4,
    "grid_h": 3,
    "data_query": {"maine_photos": {"source": "photos", "location": "Maine", "limit": 9}},
    "css": (
        "body{margin:0;padding:6px}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:4px}"
        ".grid img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;display:block}"
    ),
    "html": "<div class='grid' id='root'></div>",
    "js": (
        "const r=document.getElementById('root');"
        "(window.__DATA__.maine_photos||[]).forEach(p=>{const i=document.createElement('img');"
        "i.src=p.url;i.alt=p.location||'';i.onerror=()=>{i.src='/placeholder';};r.appendChild(i);});"
    ),
}

_HERO = {
    "title": "Hero photo",
    "grid_w": 6,
    "grid_h": 2,
    "data_query": {"featured": {"source": "photos", "location": "Bar Harbor", "year": 2023, "limit": 1}},
    "css": (
        "html,body{margin:0;height:100%}"
        ".hero{position:relative;height:100%;min-height:120px}"
        ".hero img{width:100%;height:100%;object-fit:cover;display:block}"
        ".cap{position:absolute;left:0;right:0;bottom:0;padding:12px;color:white;font-size:14px;"
        "font-family:-apple-system,sans-serif;background:linear-gradient(transparent,rgba(0,0,0,.6))}"
    ),
    "html": "<div class='hero' id='root'></div>",
    "js": (
        "const p=(window.__DATA__.featured||[])[0];const r=document.getElementById('root');"
        "if(p){const i=document.createElement('img');i.src=p.url;i.onerror=()=>{i.src='/placeholder';};"
        "const c=document.createElement('div');c.className='cap';c.textContent=p.location||''; throw Error;"
        "r.appendChild(i);r.appendChild(c);}"
    ),
}

_POLAROID = {
    "title": "Polaroid wall",
    "grid_w": 4,
    "grid_h": 4,
    "data_query": {"camden_photos": {"source": "photos", "location": "Camden", "limit": 6}},
    "css": (
        "body{margin:0;padding:8px;background:#f5f3ef;font-family:-apple-system,sans-serif}"
        ".wall{display:flex;flex-wrap:wrap;gap:10px}"
        ".pol{background:white;padding:6px 6px 16px;box-shadow:0 1px 5px rgba(0,0,0,.2);transform:rotate(var(--r))}"
        ".pol img{width:90px;height:90px;object-fit:cover;display:block}"
        ".lbl{font-size:10px;color:#666;text-align:center;margin-top:4px}"
    ),
    "html": "<div class='wall' id='root'></div>",
    "js": (
        "const r=document.getElementById('root');"
        "(window.__DATA__.camden_photos||[]).forEach((p,n)=>{const d=document.createElement('div');"
        "d.className='pol';d.style.setProperty('--r',((n%5)-2)*2+'deg');"
        "const i=document.createElement('img');i.src=p.url;i.onerror=()=>{i.src='/placeholder';};"
        "const c=document.createElement('div');c.className='lbl';c.textContent=p.location||'';"
        "d.appendChild(i);d.appendChild(c);r.appendChild(d);});"
    ),
}

_FILMSTRIP = {
    "title": "Filmstrip",
    "grid_w": 6,
    "grid_h": 2,
    "data_query": {"recent_photos": {"source": "photos", "order_by": "date", "limit": 8}},
    "css": (
        "body{margin:0;background:#111;height:100vh}"
        ".strip{display:flex;gap:4px;overflow-x:auto;height:100%;align-items:center;padding:8px}"
        ".strip img{height:88%;width:auto;object-fit:cover;border-radius:2px;display:block}"
    ),
    "html": "<div class='strip' id='root'></div>",
    "js": (
        "const r=document.getElementById('root');"
        "(window.__DATA__.recent_photos||[]).forEach(p=>{const i=document.createElement('img');"
        "i.src=p.url;i.onerror=()=>{i.src='/placeholder';};r.appendChild(i);});"
    ),
}

_COLLAGE = {
    "title": "Collage",
    "grid_w": 4,
    "grid_h": 4,
    "data_query": {"acadia_photos": {"source": "photos", "location": "Acadia NP", "limit": 5}},
    "css": (
        "body{margin:0;padding:4px}"
        ".collage{display:grid;grid-template-columns:repeat(3,1fr);grid-auto-rows:60px;gap:4px}"
        ".collage img{width:100%;height:100%;object-fit:cover;display:block;border-radius:3px}"
        ".feat{grid-column:span 2;grid-row:span 2}"
    ),
    "html": "<div class='collage' id='root'></div>",
    "js": (
        "const r=document.getElementById('root');"
        "(window.__DATA__.acadia_photos||[]).forEach((p,n)=>{const i=document.createElement('img');"
        "i.src=p.url;i.onerror=()=>{i.src='/placeholder';};if(n===0)i.className='feat';r.appendChild(i);});"
    ),
}

# ---- Filterable widgets: pre-load a dataset + facets, filter client-side ----

_FILTER_GRID_CSS = (
    "body{margin:0;font-family:-apple-system,sans-serif}"
    ".bar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:8px;border-bottom:1px solid #eee}"
    ".bar select,.bar input{font-size:12px;padding:4px 6px;border:1px solid #ccc;border-radius:6px}"
    ".bar input{flex:1;min-width:120px}"
    ".tags{display:flex;gap:4px;flex-wrap:wrap}"
    ".chip,.who{font-size:11px;border:1px solid #ccc;background:white;border-radius:12px;padding:2px 8px;cursor:pointer}"
    ".chip.on{background:#007BFF;color:white;border-color:#007BFF}"
    ".who.on{background:#6c5ce7;color:white;border-color:#6c5ce7}"
    ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(70px,1fr));gap:4px;padding:8px}"
    ".grid img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;display:block}"
)

_FILTERABLE_GALLERY = {
    "title": "Filterable gallery",
    "grid_w": 6,
    "grid_h": 4,
    "data_query": {
        "all_photos": {"source": "photos", "limit": 15},
        "facets": {"source": "facets"},
    },
    "css": _FILTER_GRID_CSS,
    "html": (
        "<div class='bar'><select id='year'></select>"
        "<div class='tags' id='tags'></div></div><div class='grid' id='grid'></div>"
    ),
    "js": (
        "const d=window.__DATA__;const f=d.facets||{};const active=new Set();"
        "const yearSel=document.getElementById('year');const tagBox=document.getElementById('tags');"
        "const grid=document.getElementById('grid');"
        "const all=document.createElement('option');all.value='';all.textContent='All years';yearSel.appendChild(all);"
        "(f.years||[]).forEach(y=>{const o=document.createElement('option');o.value=y;o.textContent=y;yearSel.appendChild(o);});"
        "(f.tags||[]).forEach(t=>{const b=document.createElement('button');b.className='chip';b.textContent=t;"
        "b.onclick=()=>{active.has(t)?(active.delete(t),b.classList.remove('on')):(active.add(t),b.classList.add('on'));render();};"
        "tagBox.appendChild(b);});"
        "function render(){const yr=yearSel.value;grid.innerHTML='';"
        "(d.all_photos||[]).filter(p=>(!yr||String(p.year)===yr)&&[...active].every(t=>(p.tags||[]).includes(t)))"
        ".forEach(p=>{const i=document.createElement('img');i.src=p.url;i.onerror=()=>{i.src='/placeholder';};grid.appendChild(i);});}"
        "yearSel.onchange=render;render();"
    ),
}

_SEARCHABLE_GALLERY = {
    "title": "Searchable gallery",
    "grid_w": 6,
    "grid_h": 4,
    "data_query": {"all_photos": {"source": "photos", "limit": 15}},
    "css": _FILTER_GRID_CSS,
    "html": (
        "<div class='bar'><input id='q' placeholder='Search location, tag, or person…'></div>"
        "<div class='grid' id='grid'></div>"
    ),
    "js": (
        "const d=window.__DATA__;const inp=document.getElementById('q');const grid=document.getElementById('grid');"
        "function render(){const q=inp.value.toLowerCase();grid.innerHTML='';"
        "(d.all_photos||[]).filter(p=>!q||((p.location||'')+' '+(p.tags||[]).join(' ')+' '+(p.persons||[]).join(' '))"
        ".toLowerCase().includes(q)).forEach(p=>{const i=document.createElement('img');i.src=p.url;"
        "i.onerror=()=>{i.src='/placeholder';};grid.appendChild(i);});}"
        "inp.oninput=render;render();"
    ),
}

_PEOPLE_PICKER = {
    "title": "People picker",
    "grid_w": 6,
    "grid_h": 4,
    "data_query": {
        "people": {"source": "persons", "limit": 6},
        "people_photos": {"source": "photos", "limit": 15},
    },
    "css": _FILTER_GRID_CSS,
    "html": "<div class='bar' id='bar'></div><div class='grid' id='grid'></div>",
    "js": (
        "const d=window.__DATA__;const bar=document.getElementById('bar');const grid=document.getElementById('grid');let sel=null;"
        "(d.people||[]).forEach(pr=>{const b=document.createElement('button');b.className='who';b.textContent=pr.name;"
        "b.onclick=()=>{sel=sel===pr.name?null:pr.name;[...bar.children].forEach(c=>c.classList.toggle('on',c.textContent===sel));render();};"
        "bar.appendChild(b);});"
        "function render(){grid.innerHTML='';(d.people_photos||[]).filter(p=>!sel||(p.persons||[]).includes(sel))"
        ".forEach(p=>{const i=document.createElement('img');i.src=p.url;i.onerror=()=>{i.src='/placeholder';};grid.appendChild(i);});}"
        "render();"
    ),
}

# Server-filtered: loads all people up front, but fetches each person's photos
# live from the server (via the parent postMessage broker) when clicked.
_PERSON_BROWSER = {
    "title": "Person browser",
    "grid_w": 6,
    "grid_h": 4,
    "data_query": {"all_people": {"source": "persons", "limit": 6}},
    "css": _FILTER_GRID_CSS,
    "html": "<div class='bar' id='bar'></div><div class='grid' id='grid'></div>",
    "js": (
        "const d=window.__DATA__;const bar=document.getElementById('bar');const grid=document.getElementById('grid');"
        "let reqId=0;const pending={};"
        "window.addEventListener('message',e=>{const m=e.data||{};"
        "if(m.type==='yaffo:result'&&pending[m.requestId]){pending[m.requestId](m.data);delete pending[m.requestId];}});"
        "function serverQuery(query){return new Promise(res=>{const id=++reqId;pending[id]=res;"
        "parent.postMessage({type:'yaffo:query',requestId:id,query},'*');});}"
        "(d.all_people||[]).forEach(pr=>{const b=document.createElement('button');b.className='who';b.textContent=pr.name;"
        "b.onclick=async()=>{[...bar.children].forEach(c=>c.classList.toggle('on',c===b));grid.textContent='Loading…';"
        "const photos=await serverQuery({source:'photos',person:pr.name,limit:12});grid.innerHTML='';"
        "(photos||[]).forEach(p=>{const i=document.createElement('img');i.src=p.url;"
        "i.onerror=()=>{i.src='/placeholder';};grid.appendChild(i);});};bar.appendChild(b);});"
    ),
}

# Pub/sub: a global filter publishes filter changes on the "filter" topic; any
# number of linked galleries subscribe and re-query the server when it changes.
_GLOBAL_FILTER = {
    "title": "Global filter",
    "grid_w": 12,
    "grid_h": 1,
    "data_query": {"facets": {"source": "facets"}},
    "css": (
        "body{margin:0;font-family:-apple-system,sans-serif}"
        ".bar{display:flex;align-items:center;gap:12px;padding:10px}"
        ".bar label{font-size:12px;color:#666}"
        ".bar select{font-size:13px;padding:4px 6px;border:1px solid #ccc;border-radius:6px}"
    ),
    "html": "<div class='bar' id='root'></div>",
    "js": (
        "const f=window.__DATA__.facets||{};const st=window.__STATE__||{};const root=document.getElementById('root');"
        "function sel(label,opts,val){const w=document.createElement('label');w.textContent=label+': ';"
        "const s=document.createElement('select');const a=document.createElement('option');a.value='';a.textContent='All';s.appendChild(a);"
        "opts.forEach(o=>{const e=document.createElement('option');e.value=o;e.textContent=o;s.appendChild(e);});"
        "s.value=val||'';w.appendChild(s);root.appendChild(w);return s;}"
        "const loc=sel('Location',f.locations||[],st.location);const yr=sel('Year',(f.years||[]).map(String),st.year!=null?String(st.year):'');"
        "function onChange(){const flt={location:loc.value||null,year:yr.value?Number(yr.value):null};"
        "parent.postMessage({type:'yaffo:publish',topic:'filter',payload:flt},'*');"
        "parent.postMessage({type:'yaffo:state',state:flt},'*');}"
        "loc.onchange=onChange;yr.onchange=onChange;"
    ),
}

_LINKED_GALLERY = {
    "title": "Linked gallery",
    "grid_w": 4,
    "grid_h": 3,
    "data_query": {"initial": {"source": "photos", "limit": 12}},
    "css": (
        "body{margin:0;padding:6px}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(70px,1fr));gap:4px}"
        ".grid img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;display:block}"
    ),
    "html": "<div class='grid' id='root'></div>",
    "js": (
        "const d=window.__DATA__;const st=window.__STATE__||{};const grid=document.getElementById('root');"
        "let reqId=0;const pending={};"
        "function draw(photos){grid.innerHTML='';(photos||[]).forEach(p=>{const i=document.createElement('img');"
        "i.src=p.url;i.onerror=()=>{i.src='/placeholder';};grid.appendChild(i);});}"
        "function serverQuery(query){return new Promise(res=>{const id=++reqId;pending[id]=res;"
        "parent.postMessage({type:'yaffo:query',requestId:id,query},'*');});}"
        "async function applyFilter(fl){const q={source:'photos',limit:12};"
        "if(fl.location)q.location=fl.location;if(fl.year)q.year=fl.year;draw(await serverQuery(q));}"
        "window.addEventListener('message',async e=>{const m=e.data||{};"
        "if(m.type==='yaffo:result'&&pending[m.requestId]){pending[m.requestId](m.data);delete pending[m.requestId];return;}"
        "if(m.type==='yaffo:event'&&m.topic==='filter'){const fl=m.payload||{};"
        "parent.postMessage({type:'yaffo:state',state:{filter:fl}},'*');applyFilter(fl);}});"
        "if(st.filter){applyFilter(st.filter);}else{draw(d.initial||[]);}"
    ),
}

# Intentionally throws, to exercise the per-widget error boundary in the frame.
_BROKEN = {
    "title": "Broken widget (demo)",
    "grid_w": 4,
    "grid_h": 2,
    "data_query": {},
    "css": "body{margin:0;font-family:-apple-system,sans-serif}",
    "html": "<div id='root'>about to fail…</div>",
    "js": (
        "document.getElementById('root').textContent='running…';"
        "throw new Error('Intentional demo error: this widget always fails');"
    ),
}

_WIDGET_STUBS = [
    # _PHOTO_GRID,
    # _HERO,
    # _POLAROID,
    # _FILMSTRIP,
    # _COLLAGE,
    # _FILTERABLE_GALLERY,
    # _SEARCHABLE_GALLERY,
    # _PEOPLE_PICKER,
    # _PERSON_BROWSER,
    _GLOBAL_FILTER,
    _LINKED_GALLERY,
    _BROKEN,
]
current_stub_index = 0

# ---------------------------------------------------------------------------
# Page / widget operations
# ---------------------------------------------------------------------------

def list_pages() -> list[GenPage]:
    return sorted(_pages.values(), key=lambda p: p.updated_at, reverse=True)


def get_page(page_id: int) -> Optional[GenPage]:
    return _pages.get(page_id)


def create_page(title: str, theme_prompt: str = "") -> GenPage:
    page = GenPage(id=next(_page_ids), title=title, theme_prompt=theme_prompt)
    _pages[page.id] = page
    return page


def remove_widget(page_id: int, widget_id: str) -> None:
    page = _pages.get(page_id)
    if page is None:
        return
    page.widgets = [w for w in page.widgets if w.id != widget_id]
    page.updated_at = datetime.now()


def save_page_widgets(page_id: int, widgets: list[dict]) -> None:
    """Commit the page's full widget set from the client (the Save button) — the
    only thing that writes widget content. The client is the source of truth for
    which widgets exist and their layout: each entry carries layout always, plus
    content for widgets the client holds (generated/edited drafts). Content the
    client omits falls back to the widget's currently-saved value; widgets absent
    from the list are dropped. Generation never writes here."""
    page = _pages.get(page_id)
    if page is None:
        return
    existing = {w.id: w for w in page.widgets}
    new_widgets: list[GenWidget] = []
    for item in widgets:
        wid = str(item.get("id") or new_widget_id())
        base = existing.get(wid) or GenWidget(id=wid)
        new_widgets.append(GenWidget(
            id=wid,
            title=item.get("title", base.title),
            prompt=base.prompt,
            data_query=item.get("data_query", base.data_query) or {},
            state=item.get("state", base.state) or {},
            html=item.get("html", base.html),
            css=item.get("css", base.css),
            js=item.get("js", base.js),
            status="ready",
            grid_x=int(item.get("x", base.grid_x)),
            grid_y=int(item.get("y", base.grid_y)),
            grid_w=int(item.get("w", base.grid_w)),
            grid_h=int(item.get("h", base.grid_h)),
        ))
    page.widgets = new_widgets
    page.updated_at = datetime.now()


def merge_widget_content(stored_widgets: list, client_widgets: list[dict]) -> list[dict]:
    """The current content of each widget on the client's grid: stored content
    overlaid with any client-provided fields (unsaved drafts win), in client
    order. Read-only — nothing is persisted. Gives the model the *real* current
    html/css/js when editing, so it doesn't rewrite code it can't see.

    `stored_widgets` is the page's persisted widgets (storage-agnostic: any object
    exposing the widget fields), so the caller owns where they come from."""
    existing = {w.id: w for w in stored_widgets}
    resolved: list[dict] = []
    for item in client_widgets:
        wid = str(item.get("id") or "")
        base = existing.get(wid) or GenWidget(id=wid)
        resolved.append({
            "id": wid,
            "title": item.get("title", base.title),
            "prompt": base.prompt,
            "data_query": item.get("data_query", base.data_query) or {},
            "state": item.get("state", base.state) or {},
            "html": item.get("html", base.html),
            "css": item.get("css", base.css),
            "js": item.get("js", base.js),
            "grid_w": int(item.get("w", base.grid_w)),
            "grid_h": int(item.get("h", base.grid_h)),
        })
    return resolved


def add_message(page_id: int, role: str, content: str) -> Optional[GenMessage]:
    page = _pages.get(page_id)
    if page is None:
        return None
    message = GenMessage(role=role, content=content)
    page.messages.append(message)
    page.updated_at = datetime.now()
    return message


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