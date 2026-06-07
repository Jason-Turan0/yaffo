#!/usr/bin/env python3
"""Seed a few example AI-builder pages (CustomPage + widgets).

Stand-ins for what the model would generate, so the page builder has something to
show without an API key. Modeled on the old stub-store widget catalog, but written
against the *current* contract: real sources + operator filters (data_query
validated by data_query_repository), and the window.yaffo widget API
(yaffo.data[name], yaffo.query, yaffo.publish/subscribe, yaffo.saveState,
yaffo.photoUrl) — see yaffo/static/pages/widget_api.js.

Run:
    python -m yaffo.scripts.seed_pages

Idempotent: re-running replaces any seeded page with the same title.
"""
from __future__ import annotations

from yaffo.app import create_app
from yaffo.db import db
from yaffo.db.repositories import custom_page_repository as page_repo

# Square-thumbnail grid, shared by the simple photo widgets.
_GRID_CSS = (
    "body{margin:0;padding:6px;font-family:-apple-system,sans-serif}"
    ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:4px}"
    ".grid img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;display:block;background:#eee}"
)

# Reused in several widgets: turn a photos row into an <img> by its id.
_IMG_FROM_PHOTO = (
    "const i=document.createElement('img');i.src=yaffo.photoUrl(p.id);"
    "i.loading='lazy';i.onerror=()=>{i.src='/placeholder';};"
)


def _photo_grid() -> dict:
    return {
        "title": "Photo grid",
        "data_query": {"photos": {"source": "photos", "limit": 12}},
        "css": _GRID_CSS,
        "html": "<div class='grid' id='root'></div>",
        "js": (
            "const r=document.getElementById('root');"
            "(yaffo.data.photos||[]).forEach(p=>{" + _IMG_FROM_PHOTO + "r.appendChild(i);});"
        ),
    }


def _hero() -> dict:
    return {
        "title": "Hero photo",
        "data_query": {"featured": {"source": "photos", "limit": 1}},
        "css": (
            "html,body{margin:0;height:100%}"
            ".hero{position:relative;height:100%;min-height:140px;background:#111}"
            ".hero img{width:100%;height:100%;object-fit:cover;display:block}"
            ".cap{position:absolute;left:0;right:0;bottom:0;padding:12px;color:#fff;font:14px -apple-system,sans-serif;"
            "background:linear-gradient(transparent,rgba(0,0,0,.6))}"
        ),
        "html": "<div class='hero' id='root'></div>",
        "js": (
            "const p=(yaffo.data.featured||[])[0];const r=document.getElementById('root');"
            "if(p){" + _IMG_FROM_PHOTO + "const c=document.createElement('div');c.className='cap';"
            "c.textContent=[p.location_name,p.year].filter(Boolean).join(' \\u00b7 ');"
            "r.appendChild(i);r.appendChild(c);}"
        ),
    }


def _polaroid() -> dict:
    return {
        "title": "Polaroid wall",
        "data_query": {"shots": {"source": "photos", "limit": 8}},
        "css": (
            "body{margin:0;padding:8px;background:#f5f3ef;font-family:-apple-system,sans-serif}"
            ".wall{display:flex;flex-wrap:wrap;gap:10px}"
            ".pol{background:#fff;padding:6px 6px 16px;box-shadow:0 1px 5px rgba(0,0,0,.2);transform:rotate(var(--r))}"
            ".pol img{width:96px;height:96px;object-fit:cover;display:block;background:#eee}"
            ".lbl{font-size:10px;color:#666;text-align:center;margin-top:4px}"
        ),
        "html": "<div class='wall' id='root'></div>",
        "js": (
            "const r=document.getElementById('root');"
            "(yaffo.data.shots||[]).forEach((p,n)=>{const d=document.createElement('div');d.className='pol';"
            "d.style.setProperty('--r',((n%5)-2)*2+'deg');" + _IMG_FROM_PHOTO
            + "const c=document.createElement('div');c.className='lbl';c.textContent=p.year||'';"
            "d.appendChild(i);d.appendChild(c);r.appendChild(d);});"
        ),
    }


def _filmstrip() -> dict:
    return {
        "title": "Filmstrip",
        "data_query": {"strip": {"source": "photos", "limit": 15}},
        "css": (
            "body{margin:0;background:#111;height:100vh}"
            ".strip{display:flex;gap:4px;overflow-x:auto;height:100%;align-items:center;padding:8px}"
            ".strip img{height:88%;width:auto;object-fit:cover;border-radius:2px;display:block;background:#222}"
        ),
        "html": "<div class='strip' id='root'></div>",
        "js": (
            "const r=document.getElementById('root');"
            "(yaffo.data.strip||[]).forEach(p=>{" + _IMG_FROM_PHOTO + "r.appendChild(i);});"
        ),
    }


def _filterable_gallery() -> dict:
    # Pre-loads a broad set + a year facet, filters client-side.
    return {
        "title": "Filterable gallery",
        "data_query": {
            "all": {"source": "photos", "limit": 40},
            "years": {"source": "photos", "op": "facet", "field": "year"},
        },
        "css": _GRID_CSS + (
            ".bar{display:flex;gap:8px;align-items:center;padding:6px 2px}"
            ".bar select{font-size:12px;padding:4px 6px;border:1px solid #ccc;border-radius:6px}"
        ),
        "html": "<div class='bar'><label>Year <select id='year'></select></label></div><div class='grid' id='grid'></div>",
        "js": (
            "const d=yaffo.data;const sel=document.getElementById('year');const grid=document.getElementById('grid');"
            "const a=document.createElement('option');a.value='';a.textContent='All years';sel.appendChild(a);"
            "(d.years||[]).filter(y=>y.value!=null).sort((x,y)=>y.value-x.value).forEach(y=>{"
            "const o=document.createElement('option');o.value=y.value;o.textContent=y.value+' ('+y.count+')';sel.appendChild(o);});"
            "function render(){const yr=sel.value;grid.innerHTML='';"
            "(d.all||[]).filter(p=>!yr||String(p.year)===yr).forEach(p=>{" + _IMG_FROM_PHOTO + "grid.appendChild(i);});}"
            "sel.onchange=render;render();"
        ),
    }


def _stats() -> dict:
    return {
        "title": "Library stats",
        "data_query": {
            "total": {"source": "photos", "op": "count"},
            "people": {"source": "people", "op": "count"},
            "years": {"source": "photos", "op": "facet", "field": "year"},
        },
        "css": (
            "body{margin:0;font-family:-apple-system,sans-serif;display:flex;height:100vh;align-items:center}"
            ".row{display:flex;gap:24px;justify-content:center;width:100%}"
            ".stat{text-align:center}.num{font-size:32px;font-weight:700;color:#222}"
            ".lbl{font-size:12px;color:#888;text-transform:uppercase;letter-spacing:.05em}"
        ),
        "html": "<div class='row' id='root'></div>",
        "js": (
            "const d=yaffo.data;const root=document.getElementById('root');"
            "const yrs=(d.years||[]).map(y=>y.value).filter(v=>v!=null);"
            "const span=yrs.length?Math.min(...yrs)+'\\u2013'+Math.max(...yrs):'\\u2014';"
            "[['Photos',d.total],['People',d.people],['Years',span]].forEach(([label,val])=>{"
            "const c=document.createElement('div');c.className='stat';"
            "const n=document.createElement('div');n.className='num';n.textContent=val!=null?val:'\\u2014';"
            "const l=document.createElement('div');l.className='lbl';l.textContent=label;"
            "c.appendChild(n);c.appendChild(l);root.appendChild(c);});"
        ),
    }


def _global_filter() -> dict:
    # Publishes filter changes on the 'filter' topic; linked galleries subscribe.
    return {
        "title": "Global filter",
        "data_query": {
            "years": {"source": "photos", "op": "facet", "field": "year"},
            "locs": {"source": "photos", "op": "facet", "field": "location_name"},
        },
        "css": (
            "body{margin:0;font-family:-apple-system,sans-serif}"
            ".bar{display:flex;gap:14px;align-items:center;padding:10px}"
            ".bar label{font-size:12px;color:#666}"
            ".bar select{font-size:13px;padding:4px 6px;border:1px solid #ccc;border-radius:6px}"
        ),
        "html": "<div class='bar' id='root'></div>",
        "js": (
            "const f=yaffo.data;const st=yaffo.state||{};const root=document.getElementById('root');"
            "function sel(label,opts,val){const w=document.createElement('label');w.textContent=label+': ';"
            "const s=document.createElement('select');const a=document.createElement('option');a.value='';a.textContent='All';s.appendChild(a);"
            "opts.forEach(o=>{const e=document.createElement('option');e.value=o;e.textContent=o;s.appendChild(e);});"
            "s.value=val||'';w.appendChild(s);root.appendChild(w);return s;}"
            "const years=(f.years||[]).map(y=>y.value).filter(v=>v!=null).sort((a,b)=>b-a);"
            "const locs=(f.locs||[]).map(l=>l.value).filter(Boolean);"
            "const yr=sel('Year',years,st.year!=null?String(st.year):'');const loc=sel('Location',locs,st.location||'');"
            "function onChange(){const flt={year:yr.value?Number(yr.value):null,location:loc.value||null};"
            "yaffo.publish('filter',flt);yaffo.saveState(flt);}"
            "yr.onchange=onChange;loc.onchange=onChange;"
        ),
    }


def _linked_gallery() -> dict:
    # Subscribes to 'filter' and re-queries the server live on each change.
    return {
        "title": "Linked gallery",
        "data_query": {"initial": {"source": "photos", "limit": 12}},
        "css": _GRID_CSS,
        "html": "<div class='grid' id='root'></div>",
        "js": (
            "const grid=document.getElementById('root');const st=yaffo.state||{};"
            "function draw(photos){grid.innerHTML='';(photos||[]).forEach(p=>{" + _IMG_FROM_PHOTO + "grid.appendChild(i);});}"
            "async function applyFilter(flt){const q={source:'photos',limit:12};"
            "if(flt.year)q.year={eq:flt.year};if(flt.location)q.location_name={eq:flt.location};"
            "draw(await yaffo.query(q));}"
            "yaffo.subscribe('filter',flt=>{yaffo.saveState({filter:flt});applyFilter(flt);});"
            "if(st.filter){applyFilter(st.filter);}else{draw(yaffo.data.initial||[]);}"
        ),
    }


def _place(widget: dict, x: int, y: int, w: int, h: int) -> dict:
    """A widget catalog entry positioned on the grid — the item shape Save posts."""
    return {**widget, "id": page_repo.new_widget_id(), "x": x, "y": y, "w": w, "h": h, "state": {}}


# Each page: a title, a theme prompt, and its positioned widgets.
PAGES: list[dict] = [
    {
        "title": "Maine Summer",
        "theme": "Highlights and a polaroid wall from our Maine trip.",
        "widgets": [
            _place(_hero(), 0, 0, 12, 2),
            _place(_photo_grid(), 0, 2, 6, 3),
            _place(_polaroid(), 6, 2, 6, 3),
        ],
    },
    {
        "title": "Browse",
        "theme": "Filter and skim the whole library.",
        "widgets": [
            _place(_filterable_gallery(), 0, 0, 12, 4),
            _place(_filmstrip(), 0, 4, 12, 2),
        ],
    },
    {
        "title": "Connected",
        "theme": "A global filter driving two linked galleries.",
        "widgets": [
            _place(_global_filter(), 0, 0, 12, 1),
            _place(_linked_gallery(), 0, 1, 6, 3),
            _place(_linked_gallery(), 6, 1, 6, 3),
        ],
    },
    {
        "title": "Overview",
        "theme": "The library at a glance.",
        "widgets": [
            _place(_stats(), 0, 0, 12, 2),
            _place(_photo_grid(), 0, 2, 12, 3),
        ],
    },
]


def seed_pages() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()
        existing = {p.title: p.id for p in page_repo.list_pages(db.session)}
        for spec in PAGES:
            if spec["title"] in existing:  # idempotent: replace a prior seed
                page_repo.delete_page(db.session, existing[spec["title"]])
            page = page_repo.create_page(db.session, title=spec["title"], subtitle=spec["theme"])
            page_repo.save_page_widgets(db.session, page.id, spec["widgets"])
            print(f"Seeded '{page.title}' (id={page.id}) with {len(spec['widgets'])} widgets")
        print(f"Done: {len(PAGES)} pages.")


if __name__ == "__main__":
    seed_pages()