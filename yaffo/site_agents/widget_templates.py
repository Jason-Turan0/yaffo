"""Curated widget templates for the AI page builder.

A small, hand-styled catalog the model can adapt instead of inventing a look from
scratch (which drifts all over the place). Every template:

- uses the **real data contract** — named `data_query`s over the exposed sources
  (`photos`, `people`, `people_face`, …; see data_query_repository) and the
  in-iframe `window.yaffo` API (`yaffo.data[name]`, `yaffo.query`, `yaffo.mediaUrl`,
  `yaffo.publish`/`subscribe`, `yaffo.saveState`);
- writes only its *specific* CSS, in terms of the **design tokens**: the widget
  frame loads `static/tokens.css` with the active theme stamped on
  `<html data-theme>`, plus the app baseline (`widget_theme.WIDGET_THEME_CSS`), so
  templates style with `var(--color-…)` / `var(--radius-…)` / `var(--font-size-…)`
  / `var(--shadow-…)` and follow whichever theme is active. Raw color values are
  reserved for content that sits *on top of photos* (caption scrims, lightbox
  chrome), which must stay legible regardless of theme.

This is the single source: the seed script renders them onto a page, and the
template tool (presented to the agent) serves the same list.
"""
from __future__ import annotations

from dataclasses import dataclass

from yaffo.db.repositories import custom_page_repository as page_repo


@dataclass
class WidgetTemplate:
    """One reusable widget design. `description` is what the agent sees in the tool
    (so it can pick); `data_query`/`html`/`css`/`js` are a ready-to-use example. `css`
    is just the template's own rules — the frame supplies the shared baseline."""
    name: str
    description: str
    data_query: dict
    html: str
    css: str
    js: str
    # A suggested grid size, used when seeding the showcase page.
    grid_w: int = 6
    grid_h: int = 4

    def to_widget_item(self, x: int, y: int) -> dict:
        """The client widget-entry shape Save posts (for the seed page)."""
        return {
            "id": page_repo.new_widget_id(),
            "title": self.name,
            "data_query": self.data_query,
            "html": self.html,
            "css": self.css,
            "js": self.js,
            "state": {},
            "x": x, "y": y, "w": self.grid_w, "h": self.grid_h,
        }


# --- templates -------------------------------------------------------------

_PHOTO_GRID = WidgetTemplate(
    name="Photo grid",
    description=(
        "A responsive grid of photo thumbnails with rounded corners. Good default for "
        "showing a set of photos. One query over `photos`."
    ),
    data_query={"photos": {"source": "media_items", "limit": 24}},
    html="<div class='grid' id='root'></div>",
    css="""\
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 8px; }
.grid img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: var(--radius-md); display: block; }
""",
    js="""\
const root = document.getElementById('root');
const photos = yaffo.data.photos || [];
if (!photos.length) { root.innerHTML = "<div class='yf-empty'>No photos to show.</div>"; }
photos.forEach((p) => {
  const img = document.createElement('img');
  img.src = yaffo.mediaUrl(p.id);
  img.loading = 'lazy';
  img.alt = p.location_name || '';
  root.appendChild(img);
});
""",
    grid_w=6, grid_h=4,
)


_STATS = WidgetTemplate(
    name="Library stats",
    description=(
        "A row of stat tiles (big number + uppercase label) for at-a-glance counts. "
        "Uses aggregate queries (`op: count` and `op: facet`)."
    ),
    data_query={
        "photos": {"source": "media_items", "op": "count"},
        "people": {"source": "people", "op": "count"},
        "years": {"source": "media_items", "op": "facet", "field": "year"},
    },
    html="<div class='tiles' id='root'></div>",
    css="""\
.tiles { display: flex; gap: 12px; flex-wrap: wrap; }
.tile { flex: 1; min-width: 96px; background: var(--color-bg); border: var(--border-width) solid var(--color-border);
        border-radius: var(--radius-md); padding: 16px; text-align: center; }
.tile .num { font-size: var(--font-size-2xl); font-weight: 700; color: var(--color-text); line-height: 1; }
.tile .lbl { margin-top: 6px; font-size: var(--font-size-xs); color: var(--color-text-muted);
             text-transform: uppercase; letter-spacing: 0.04em; }
""",
    js="""\
const root = document.getElementById('root');
const d = yaffo.data;
const years = (d.years || []).map((y) => y.value).filter((v) => v != null);
const span = years.length ? Math.min(...years) + '\\u2013' + Math.max(...years) : '\\u2014';
const tiles = [['Photos', d.photos], ['People', d.people], ['Years', span]];
tiles.forEach(([label, value]) => {
  const tile = document.createElement('div');
  tile.className = 'tile';
  tile.innerHTML = "<div class='num'></div><div class='lbl'></div>";
  tile.querySelector('.num').textContent = value != null ? value : '\\u2014';
  tile.querySelector('.lbl').textContent = label;
  root.appendChild(tile);
});
""",
    grid_w=12, grid_h=2,
)


_FEATURED = WidgetTemplate(
    name="Featured photo",
    description=(
        "A single full-bleed hero photo with a caption overlay (location · year). "
        "Use for a focal image at the top of a page. One query, limit 1."
    ),
    data_query={"featured": {"source": "media_items", "limit": 1}},
    html="<div class='hero' id='root'></div>",
    css="""\
body { padding: 0; }
.hero { position: relative; width: 100%; height: 100%; min-height: 160px; background: var(--color-surface-sunken); }
.hero img { width: 100%; height: 100%; object-fit: cover; display: block; }
/* Caption scrim sits on the photo itself, so it stays white-on-dark in every theme. */
.hero .cap { position: absolute; left: 0; right: 0; bottom: 0; padding: 12px 14px; color: #fff;
             font-size: var(--font-size-sm); font-weight: 500; background: linear-gradient(transparent, rgba(0,0,0,0.55)); }
.hero .yf-empty { padding: 14px; }
""",
    js="""\
const root = document.getElementById('root');
const p = (yaffo.data.featured || [])[0];
if (!p) { root.innerHTML = "<div class='yf-empty'>No photo selected.</div>"; }
else {
  const img = document.createElement('img');
  img.src = yaffo.mediaUrl(p.id);
  const cap = document.createElement('div');
  cap.className = 'cap';
  cap.textContent = [p.location_name, p.year].filter(Boolean).join(' \\u00b7 ');
  root.appendChild(img);
  if (cap.textContent) root.appendChild(cap);
}
""",
    grid_w=6, grid_h=4,
)


_FILTERABLE_GALLERY = WidgetTemplate(
    name="Filterable gallery",
    description=(
        "A photo grid with a year filter built on the app's searchable-select "
        "component (load /static/searchable-select.css + .js; never use a bare native "
        "<select> — it isn't themed). The `year` facet supplies the options; changing "
        "it re-queries the server (yaffo.query with a `{eq}` filter) so it filters the "
        "whole library, not a preloaded slice. Use this pattern whenever the filter "
        "options can exceed what's first loaded."
    ),
    data_query={
        "years": {"source": "media_items", "op": "facet", "field": "year"},
    },
    html="""\
<link rel="stylesheet" href="/static/searchable-select.css">
<div class='bar'>
  <label for='year'>Year</label>
  <select id='year' class='searchable-select' data-search-disabled></select>
</div>
<div class='grid' id='grid'></div>
<script src="/static/searchable-select.js"></script>
""",
    css="""\
.bar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.bar label { font-size: var(--font-size-xs); color: var(--color-text-muted); }
.bar .searchable-select-wrapper { width: 180px; }
/* The dropdown can't escape the widget iframe, so keep it short enough to fit. */
.bar .searchable-select-options { max-height: 160px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(88px, 1fr)); gap: 8px; }
.grid img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: var(--radius-md); display: block; }
""",
    js="""\
const sel = document.getElementById('year');
const grid = document.getElementById('grid');
const opt = (value, label) => { const o = document.createElement('option'); o.value = value; o.textContent = label; return o; };
sel.appendChild(opt('', 'All years'));
(yaffo.data.years || []).filter((y) => y.value != null).sort((a, b) => b.value - a.value)
  .forEach((y) => sel.appendChild(opt(y.value, y.value + ' (' + y.count + ')')));
// Upgrade after the options exist; falls back to the native select if the script failed.
if (window.SearchableSelect) SearchableSelect.init(sel);
function draw(rows) {
  grid.innerHTML = '';
  if (!rows || !rows.length) { grid.innerHTML = "<div class='yf-empty'>No photos to show.</div>"; return; }
  rows.forEach((p) => { const img = document.createElement('img'); img.src = yaffo.mediaUrl(p.id); img.loading = 'lazy'; grid.appendChild(img); });
}
async function render() {
  const year = sel.value;
  const query = { source: 'media_items', limit: 60 };
  if (year) query.year = { eq: Number(year) };  // re-query the server, not a preloaded slice
  draw(await yaffo.query(query));
}
sel.onchange = render;
render();
""",
    grid_w=12, grid_h=4,
)


_PEOPLE = WidgetTemplate(
    name="People",
    description=(
        "Selectable person pills, each showing that person's distinct photo count; "
        "picking one shows their photos. Per-person photo counts span tables (no joins), "
        "so the bridge is stitched client-side once: load `people_face` (person↔face) and "
        "`faces` (face→photo), group distinct photo ids per person; selecting then loads "
        "that person's `photos` by id. Uses the `in` filter, yaffo.query, yaffo.saveState."
    ),
    data_query={"people": {"source": "people", "limit": 60}},
    html="""\
<div class="pills" id="pills"></div>
<div class="grid" id="grid"></div>
""",
    css="""\
.pills { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
.pill {
  display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px;
  border: var(--border-width) solid var(--color-border-strong); border-radius: var(--radius-pill);
  background: var(--color-surface); color: var(--color-text-secondary);
  font-size: var(--font-size-sm); font-family: inherit; cursor: pointer; transition: all 0.15s ease;
}
.pill:hover { border-color: var(--color-accent); color: var(--color-accent); }
.pill.active { background: var(--color-accent); border-color: var(--color-accent); color: var(--color-on-accent); }
.pill .count { font-size: var(--font-size-xs); opacity: 0.7; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 8px; }
.grid img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: var(--radius-md); display: block; }
""",
    js="""\
(async function () {
  const pillsEl = document.getElementById('pills');
  const grid = document.getElementById('grid');
  const people = (yaffo.data.people || []).filter((p) => p.name);
  if (!people.length) { pillsEl.innerHTML = "<div class='yf-empty'>No people yet.</div>"; return; }

  // No joins: stitch the bridge once to get each person's distinct photo ids.
  // people_face (person <-> face) + faces (face -> photo).
  pillsEl.innerHTML = "<div class='yf-empty'>Loading…</div>";
  const links = (await yaffo.query({ source: 'people_face', limit: 5000 })) || [];
  const faceIds = [...new Set(links.map((l) => l.face_id).filter((v) => v != null))];
  const faces = faceIds.length ? ((await yaffo.query({ source: 'faces', id: { in: faceIds }, limit: 5000 })) || []) : [];
  const faceToPhoto = {};
  faces.forEach((f) => { if (f.media_item_id != null) faceToPhoto[f.id] = f.media_item_id; });

  const photosByPerson = {};  // person_id -> distinct photo ids (insertion order)
  const seen = {};
  links.forEach((l) => {
    const photo = faceToPhoto[l.face_id];
    if (photo == null) return;
    (photosByPerson[l.person_id] = photosByPerson[l.person_id] || []);
    (seen[l.person_id] = seen[l.person_id] || new Set());
    if (!seen[l.person_id].has(photo)) { seen[l.person_id].add(photo); photosByPerson[l.person_id].push(photo); }
  });
  const photoCount = (id) => (photosByPerson[id] || []).length;

  people.sort((a, b) => photoCount(b.id) - photoCount(a.id));
  let activeId = (yaffo.state && yaffo.state.personId) || people[0].id;
  let reqToken = 0;

  function renderPills() {
    pillsEl.innerHTML = '';
    people.forEach((p) => {
      const pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'pill' + (p.id === activeId ? ' active' : '');
      pill.innerHTML = "<span class='name'></span><span class='count'></span>";
      pill.querySelector('.name').textContent = p.name;
      pill.querySelector('.count').textContent = photoCount(p.id);
      pill.addEventListener('click', () => select(p.id));
      pillsEl.appendChild(pill);
    });
  }

  async function select(personId) {
    activeId = personId;
    renderPills();
    yaffo.saveState({ personId });
    const token = ++reqToken;
    grid.innerHTML = "<div class='yf-empty'>Loading…</div>";
    const ids = (photosByPerson[personId] || []).slice(0, 60);
    const photos = ids.length ? ((await yaffo.query({ source: 'media_items', id: { in: ids }, limit: 60 })) || []) : [];
    if (token !== reqToken) return;  // a newer selection superseded this one
    grid.innerHTML = '';
    if (!photos.length) { grid.innerHTML = "<div class='yf-empty'>No photos for this person.</div>"; return; }
    photos.forEach((p) => { const img = document.createElement('img'); img.src = yaffo.mediaUrl(p.id); img.loading = 'lazy'; grid.appendChild(img); });
  }

  renderPills();
  select(activeId);
})();
""",
    grid_w=12, grid_h=5,
)


# --- richer, self-contained designs (copied from liked AI generations and
#     generalized: the trip-specific filters/ids are dropped, the CDATA artifact
#     cleaned, the title made data-driven). They bring their own layout but still
#     style with the design tokens; only photo scrims and lightbox chrome (which
#     overlay the photos themselves) keep raw colors. ----------------------------

_HERO_BANNER = WidgetTemplate(
    name="Hero banner",
    description=(
        "A full-bleed cover banner: a cover photo under a soft dark gradient, with a "
        "small accent eyebrow, a large title (the photo's location), and a date/location "
        "line. A strong page header in the app's style. One photo (limit 1)."
    ),
    data_query={"hero_photo": {"source": "media_items", "limit": 1}},
    html="""\
<div class="hero-wrap">
  <img id="hero-img" src="" alt="" onerror="this.src='/placeholder'" />
  <div class="hero-overlay">
    <div class="hero-eyebrow">My Trip to</div>
    <h1 class="hero-title" id="hero-title">Photos</h1>
    <div class="hero-meta" id="hero-meta"></div>
  </div>
</div>
""",
    css="""\
body, html { height: 100%; padding: 0; overflow: hidden; }
/* The overlay sits on the photo itself, so the scrim and white text are fixed;
   only the accent eyebrow follows the theme. */
.hero-wrap { position: relative; width: 100%; height: 100vh; min-height: 200px; overflow: hidden; background: #212529; }
#hero-img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.hero-overlay {
  position: absolute; inset: 0; display: flex; flex-direction: column; justify-content: flex-end; padding: 28px 32px;
  background: linear-gradient(to top, rgba(0,0,0,0.72) 0%, rgba(0,0,0,0.22) 55%, rgba(0,0,0,0.04) 100%);
}
.hero-eyebrow { font-size: var(--font-size-xs); font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: var(--color-accent); margin-bottom: 6px; }
.hero-title { font-size: clamp(1.8rem, 6vw, 3.2rem); font-family: var(--font-heading); font-weight: var(--font-weight-heading); color: #fff; line-height: 1.05; letter-spacing: -0.01em; }
.hero-meta { margin-top: 10px; font-size: var(--font-size-sm); color: rgba(255, 255, 255, 0.82); }
""",
    js="""\
(function () {
  const photo = (yaffo.data.hero_photo || [])[0];
  if (!photo) return;
  document.getElementById('hero-img').src = yaffo.mediaUrl(photo.id);
  if (photo.location_name) document.getElementById('hero-title').textContent = photo.location_name;
  const parts = [];
  if (photo.date_taken) {
    parts.push(yaffo.date(photo.date_taken, { year: 'numeric', month: 'long', day: 'numeric' }));
  }
  if (photo.location_name) parts.push('📍 ' + photo.location_name);
  document.getElementById('hero-meta').textContent = parts.join('  •  ');
})();
""",
    grid_w=12, grid_h=5,
)


_GALLERY = WidgetTemplate(
    name="Photo gallery",
    description=(
        "A polished gallery in the app's style: a responsive grid of rounded tiles with a "
        "subtle hover lift and a date overlay, plus a full lightbox (click a tile; arrow "
        "keys / ESC to navigate). One query over photos (add a filter like location_name "
        "to scope it)."
    ),
    data_query={"photos": {"source": "media_items", "limit": 50}},
    html="""\
<div class="gallery-page">
  <div class="gallery-header">
    <h2 class="gallery-heading">Trip Photos</h2>
    <span class="gallery-count" id="photo-count"></span>
  </div>
  <div class="gallery-grid" id="gallery-grid"></div>

  <div class="lightbox" id="lightbox" style="display:none">
    <button class="lb-close" id="lb-close">✕</button>
    <button class="lb-nav lb-prev" id="lb-prev">&#8249;</button>
    <button class="lb-nav lb-next" id="lb-next">&#8250;</button>
    <div class="lb-img-wrap">
      <img id="lb-img" src="" alt="" onerror="this.src='/placeholder'" />
    </div>
    <div class="lb-caption" id="lb-caption"></div>
  </div>
</div>
""",
    css="""\
body { padding: 0; }
.gallery-page { padding: 16px; }
.gallery-header { display: flex; align-items: baseline; gap: 10px; margin-bottom: 16px; border-bottom: var(--border-width) solid var(--color-border); padding-bottom: 12px; }
.gallery-heading { font-size: var(--font-size-md); font-family: var(--font-heading); font-weight: 600; color: var(--color-text); }
.gallery-count { font-size: var(--font-size-xs); color: var(--color-text-muted); background: var(--color-surface-sunken); border-radius: var(--radius-pill); padding: 2px 10px; }
.gallery-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
.gallery-item {
  position: relative; aspect-ratio: 1; border-radius: var(--radius-md); overflow: hidden; cursor: pointer;
  background: var(--color-surface-sunken); box-shadow: var(--shadow-sm); transition: transform 0.18s ease, box-shadow 0.18s ease;
}
.gallery-item:hover { transform: translateY(-2px); box-shadow: var(--shadow-lg); z-index: 2; }
.gallery-item img { width: 100%; height: 100%; object-fit: cover; display: block; }
.gallery-item .item-overlay {
  position: absolute; bottom: 0; left: 0; right: 0;
  background: linear-gradient(transparent, rgba(0,0,0,0.55)); padding: 18px 8px 6px; opacity: 0; transition: opacity 0.18s ease;
}
.gallery-item:hover .item-overlay { opacity: 1; }
.item-date { font-size: var(--font-size-xs); color: #fff; }
/* Lightbox is viewer chrome over the photo: it stays dark in every theme. */
.lightbox { position: fixed; inset: 0; background: rgba(0,0,0,0.9); z-index: 1000; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.lb-img-wrap { max-width: 90vw; max-height: 80vh; display: flex; align-items: center; justify-content: center; }
#lb-img { max-width: 90vw; max-height: 78vh; border-radius: var(--radius-md); box-shadow: 0 8px 40px rgba(0,0,0,0.5); object-fit: contain; }
.lb-caption { margin-top: 14px; color: rgba(255,255,255,0.75); font-size: var(--font-size-sm); }
.lb-close { position: absolute; top: 14px; right: 18px; background: none; border: none; color: #fff; font-size: 22px; line-height: 1; cursor: pointer; opacity: 0.8; transition: opacity 0.15s; }
.lb-close:hover { opacity: 1; }
.lb-nav {
  position: absolute; top: 50%; transform: translateY(-50%); background: rgba(255,255,255,0.12);
  border: 1px solid rgba(255,255,255,0.25); color: #fff; font-size: 28px; width: 40px; height: 56px;
  border-radius: var(--radius-md); cursor: pointer; display: flex; align-items: center; justify-content: center; transition: background 0.15s;
}
.lb-nav:hover { background: rgba(255,255,255,0.22); }
.lb-prev { left: 12px; }
.lb-next { right: 12px; }
""",
    js="""\
(function () {
  const photos = (yaffo.data.photos || []);
  document.getElementById('photo-count').textContent = yaffo.number(photos.length);
  const grid = document.getElementById('gallery-grid');

  function formatDate(dt) {
    if (!dt) return '';
    return yaffo.date(dt, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  photos.forEach((photo, idx) => {
    const item = document.createElement('div');
    item.className = 'gallery-item';
    const img = document.createElement('img');
    img.src = yaffo.mediaUrl(photo.id);
    img.alt = photo.location_name || '';
    img.loading = 'lazy';
    img.onerror = () => { img.src = '/placeholder'; };
    const overlay = document.createElement('div');
    overlay.className = 'item-overlay';
    const dateSpan = document.createElement('div');
    dateSpan.className = 'item-date';
    dateSpan.textContent = formatDate(photo.date_taken);
    overlay.appendChild(dateSpan);
    item.appendChild(img);
    item.appendChild(overlay);
    item.addEventListener('click', () => openLightbox(idx));
    grid.appendChild(item);
  });

  let currentIdx = 0;
  const lightbox = document.getElementById('lightbox');
  const lbImg = document.getElementById('lb-img');
  const lbCaption = document.getElementById('lb-caption');

  function showLightboxPhoto() {
    const p = photos[currentIdx];
    lbImg.src = yaffo.mediaUrl(p.id);
    const parts = [];
    if (p.date_taken) parts.push(formatDate(p.date_taken));
    if (p.location_name) parts.push('📍 ' + p.location_name);
    lbCaption.textContent = parts.join('  •  ');
  }
  function openLightbox(idx) { currentIdx = idx; showLightboxPhoto(); lightbox.style.display = 'flex'; }

  document.getElementById('lb-close').addEventListener('click', () => { lightbox.style.display = 'none'; });
  document.getElementById('lb-prev').addEventListener('click', () => { currentIdx = (currentIdx - 1 + photos.length) % photos.length; showLightboxPhoto(); });
  document.getElementById('lb-next').addEventListener('click', () => { currentIdx = (currentIdx + 1) % photos.length; showLightboxPhoto(); });
  lightbox.addEventListener('click', (e) => { if (e.target === lightbox) lightbox.style.display = 'none'; });
  document.addEventListener('keydown', (e) => {
    if (lightbox.style.display === 'none') return;
    if (e.key === 'ArrowLeft') { currentIdx = (currentIdx - 1 + photos.length) % photos.length; showLightboxPhoto(); }
    if (e.key === 'ArrowRight') { currentIdx = (currentIdx + 1) % photos.length; showLightboxPhoto(); }
    if (e.key === 'Escape') lightbox.style.display = 'none';
  });
})();
""",
    grid_w=12, grid_h=6,
)


# --- pub/sub: widgets that talk to each other over the broker. A publisher calls
#     yaffo.publish(topic, payload); subscribers call yaffo.subscribe(topic, handler).
#     The broker fans an event out to every widget on the page, so a pair only works
#     when both are on the same page and share a topic. ---------------------------

# Pair 1 (topic 'filter'): a filter bar broadcasts criteria; galleries re-query.
_FILTER_CONTROLS = WidgetTemplate(
    name="Filter controls",
    description=(
        "PUB/SUB publisher (topic 'filter'). Year + Location filters built on the "
        "app's searchable-select component (load /static/searchable-select.css + .js; "
        "never use a bare native <select> — it isn't themed); the Location one is "
        "searchable since the list can be long. Options come from facets; on change it "
        "yaffo.publish('filter', {year, location}) to every widget on the page and "
        "persists the choice. Pair it with one or more 'Linked gallery' widgets on the "
        "same page."
    ),
    data_query={
        "years": {"source": "media_items", "op": "facet", "field": "year"},
        "locs": {"source": "media_items", "op": "facet", "field": "location_name"},
    },
    html="""\
<link rel="stylesheet" href="/static/searchable-select.css">
<div class='bar' id='root'></div>
<script src="/static/searchable-select.js"></script>
""",
    css="""\
.bar { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; }
.bar label { font-size: var(--font-size-xs); color: var(--color-text-muted); display: inline-flex; align-items: center; gap: 6px; }
.bar .searchable-select-wrapper { width: 200px; }
/* The dropdown can't escape the widget iframe, so keep it short enough to fit. */
.bar .searchable-select-options { max-height: 120px; }
""",
    js="""\
const root = document.getElementById('root');
const data = yaffo.data;
const st = yaffo.state || {};
function makeSelect(labelText, options, searchable) {
  const label = document.createElement('label');
  label.textContent = labelText;
  const sel = document.createElement('select');
  sel.className = 'searchable-select';
  if (!searchable) sel.setAttribute('data-search-disabled', '');
  const all = document.createElement('option'); all.value = ''; all.textContent = 'All'; sel.appendChild(all);
  options.forEach((o) => { const e = document.createElement('option'); e.value = String(o.value); e.textContent = o.label; sel.appendChild(e); });
  label.appendChild(sel); root.appendChild(label);
  return sel;
}
const years = (data.years || []).map((y) => y.value).filter((v) => v != null).sort((a, b) => b - a).map((v) => ({ value: v, label: String(v) }));
const locs = (data.locs || []).map((l) => l.value).filter(Boolean).sort().map((v) => ({ value: v, label: v }));
const yearSel = makeSelect('Year', years, false);
const locSel = makeSelect('Location', locs, true);
if (st.year != null) yearSel.value = String(st.year);
if (st.location) locSel.value = st.location;
// Upgrade after the saved values are set so the display text starts correct;
// falls back to the native selects if the script failed.
if (window.SearchableSelect) { SearchableSelect.init(yearSel); SearchableSelect.init(locSel); }
function emit() {
  const flt = { year: yearSel.value ? Number(yearSel.value) : null, location: locSel.value || null };
  yaffo.publish('filter', flt);
  yaffo.saveState(flt);
}
yearSel.onchange = emit;
locSel.onchange = emit;
""",
    grid_w=12, grid_h=3,
)


_LINKED_GALLERY = WidgetTemplate(
    name="Linked gallery",
    description=(
        "PUB/SUB subscriber (topic 'filter'). A photo grid that yaffo.subscribe('filter', "
        "…) and re-queries on each event with the published year/location. Drop one or "
        "more on a page with a 'Filter controls' widget; several stay in sync."
    ),
    data_query={"initial": {"source": "media_items", "limit": 24}},
    html="<div class='grid' id='root'></div>",
    css="""\
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(88px, 1fr)); gap: 8px; }
.grid img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: var(--radius-md); display: block; }
""",
    js="""\
const grid = document.getElementById('root');
const st = yaffo.state || {};
function draw(photos) {
  grid.innerHTML = '';
  if (!photos || !photos.length) { grid.innerHTML = "<div class='yf-empty'>No photos match.</div>"; return; }
  photos.forEach((p) => { const img = document.createElement('img'); img.src = yaffo.mediaUrl(p.id); img.loading = 'lazy'; grid.appendChild(img); });
}
async function apply(flt) {
  yaffo.saveState({ filter: flt });
  const q = { source: 'media_items', limit: 24 };
  if (flt && flt.year) q.year = { eq: flt.year };
  if (flt && flt.location) q.location_name = { eq: flt.location };
  draw(await yaffo.query(q));
}
yaffo.subscribe('filter', apply);
if (st.filter && (st.filter.year || st.filter.location)) apply(st.filter);
else draw(yaffo.data.initial || []);
""",
    grid_w=12, grid_h=4,
)


# Pair 2 (topic 'photo'): a picker broadcasts the selected photo; a spotlight shows it.
_PHOTO_PICKER = WidgetTemplate(
    name="Photo picker",
    description=(
        "PUB/SUB publisher (topic 'photo'). A compact thumbnail grid; clicking a thumb "
        "highlights it and yaffo.publish('photo', <the photo row>) so a subscriber can "
        "react. Pair it with a 'Photo spotlight' on the same page."
    ),
    data_query={"photos": {"source": "media_items", "limit": 24}},
    html="<div class='picker' id='root'></div>",
    css="""\
.picker { display: grid; grid-template-columns: repeat(auto-fill, minmax(64px, 1fr)); gap: 6px; }
.picker img {
  width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: var(--radius-sm); display: block; cursor: pointer;
  border: 2px solid transparent; transition: border-color 0.15s ease;
}
.picker img:hover { border-color: var(--color-border-hover); }
.picker img.active { border-color: var(--color-accent); }
""",
    js="""\
const root = document.getElementById('root');
const photos = yaffo.data.photos || [];
if (!photos.length) { root.innerHTML = "<div class='yf-empty'>No photos.</div>"; }
let activeEl = null;
photos.forEach((p, idx) => {
  const img = document.createElement('img');
  img.src = yaffo.mediaUrl(p.id);
  img.loading = 'lazy';
  img.addEventListener('click', () => {
    if (activeEl) activeEl.classList.remove('active');
    img.classList.add('active');
    activeEl = img;
    yaffo.publish('photo', p);
  });
  if (idx === 0) { img.classList.add('active'); activeEl = img; }
  root.appendChild(img);
});
""",
    grid_w=6, grid_h=4,
)


_PHOTO_SPOTLIGHT = WidgetTemplate(
    name="Photo spotlight",
    description=(
        "PUB/SUB subscriber (topic 'photo'). Shows one photo large with a caption; "
        "yaffo.subscribe('photo', …) swaps it whenever a 'Photo picker' (or any widget) "
        "publishes a photo. Starts on a recent photo until a selection arrives."
    ),
    data_query={"featured": {"source": "media_items", "limit": 1}},
    html="""\
<div class="spot">
  <img id="spot-img" src="" alt="" onerror="this.src='/placeholder'" />
  <div class="spot-cap" id="spot-cap"></div>
</div>
""",
    css="""\
body { padding: 0; }
.spot { display: flex; flex-direction: column; height: 100%; }
.spot img { width: 100%; flex: 1; min-height: 0; object-fit: cover; display: block; background: var(--color-surface-sunken); }
.spot-cap { padding: 10px 14px; font-size: var(--font-size-sm); color: var(--color-text-secondary); border-top: var(--border-width) solid var(--color-border); }
""",
    js="""\
const img = document.getElementById('spot-img');
const cap = document.getElementById('spot-cap');
function show(photo) {
  if (!photo) return;
  img.src = yaffo.mediaUrl(photo.id);
  cap.textContent = [photo.location_name, photo.year].filter(Boolean).join(' · ') || ('Photo #' + photo.id);
}
yaffo.subscribe('photo', show);
show((yaffo.data.featured || [])[0]);
""",
    grid_w=6, grid_h=4,
)


# A real map of photo locations using OpenLayers (vendored under /static/vendor/ol)
# with an OpenStreetMap basemap. The widget frame CSP allows loading the app's own
# vendored ol.js/ol.css and OSM tile images; generated inline JS still has no network.
_PHOTO_MAP = WidgetTemplate(
    name="Photo map",
    description=(
        "A real interactive map (OpenLayers + OpenStreetMap basemap) of photos plotted "
        "by latitude/longitude, clustered, with pan/zoom. Click a cluster for a popup "
        "anchored over it (positioned from getPixelFromCoordinate, kept in place on "
        "postrender) showing a large preview plus a thumbnail strip you click to flip "
        "through the photos. Load "
        "the vendored library in the HTML: <link> /static/vendor/ol/10.3.1/ol.css and "
        "<script src> /static/vendor/ol/10.3.1/ol.js (the only allowed external script "
        "origin is this app; OSM tiles are allowed as images). Use ol.proj.fromLonLat for "
        "coordinates, ol.source.Cluster, and fit the view to the data."
    ),
    data_query={"photos": {"source": "media_items", "limit": 500}},
    html="""\
<link rel="stylesheet" href="/static/vendor/ol/10.3.1/ol.css">
<div id="map"></div>
<div id="popup" class="map-popup" style="display:none">
  <button type="button" class="map-popup-close" id="popup-close">✕</button>
  <img class="map-popup-main" id="popup-main" src="" alt="" onerror="this.src='/placeholder'">
  <div class="map-popup-cap" id="popup-cap"></div>
  <div class="map-popup-thumbs" id="popup-thumbs"></div>
</div>
<script src="/static/vendor/ol/10.3.1/ol.js"></script>
""",
    css="""\
html, body { height: 100%; }
body { padding: 0; position: relative; }
/* The map background matches OSM water while tiles load; the basemap doesn't theme. */
#map { width: 100%; height: 100%; background: #aadaff; }
/* Popup positioned over the cluster's pixel; the transform centers it above the
   point so the arrow points down at the circle. */
.map-popup {
  position: absolute; z-index: 5; width: 240px; padding: 8px;
  transform: translate(-50%, calc(-100% - 12px));
  background: var(--color-surface); border: var(--border-width) solid var(--color-border);
  border-radius: var(--radius-md); box-shadow: var(--shadow-xl);
}
.map-popup::before, .map-popup::after { content: ''; position: absolute; left: 50%; transform: translateX(-50%); width: 0; height: 0; }
.map-popup::before { bottom: -9px; border-left: 9px solid transparent; border-right: 9px solid transparent; border-top: 9px solid var(--color-border); }
.map-popup::after { bottom: -8px; border-left: 8px solid transparent; border-right: 8px solid transparent; border-top: 8px solid var(--color-surface); }
.map-popup-close {
  position: absolute; top: 5px; right: 6px; z-index: 1; padding: 1px 6px; line-height: 1.2; font-size: var(--font-size-sm);
  color: var(--color-text-secondary); background: var(--color-surface); border: var(--border-width) solid var(--color-border);
  border-radius: var(--radius-sm); cursor: pointer;
}
.map-popup-main { width: 100%; height: 150px; object-fit: cover; border-radius: var(--radius-sm); display: block; background: var(--color-surface-sunken); }
.map-popup-cap { font-size: var(--font-size-xs); color: var(--color-text-muted); margin: 6px 2px; }
.map-popup-thumbs { display: flex; gap: 4px; overflow-x: auto; }
.map-popup-thumbs img { width: 40px; height: 40px; object-fit: cover; border-radius: var(--radius-sm); flex: none; cursor: pointer; border: 2px solid transparent; }
.map-popup-thumbs img:hover { border-color: var(--color-border-hover); }
.map-popup-thumbs img.active { border-color: var(--color-accent); }
""",
    js="""\
(function () {
  if (!window.ol) { document.getElementById('map').innerHTML = "<div class='yf-empty' style='padding:14px'>Map library failed to load.</div>"; return; }
  const photos = (yaffo.data.photos || []).filter((p) => p.latitude != null && p.longitude != null);

  const features = photos.map((p) => new ol.Feature({
    geometry: new ol.geom.Point(ol.proj.fromLonLat([p.longitude, p.latitude])),
    photo: p,
  }));
  const points = new ol.source.Vector({ features });
  const clusters = new ol.source.Cluster({ distance: 42, source: points });

  // Cluster circles follow the active theme: read the design tokens off the
  // document, since OpenLayers canvas styles can't use var(--…) directly.
  const tokens = getComputedStyle(document.documentElement);
  const token = (name, fallback) => (tokens.getPropertyValue(name) || fallback).trim();
  const accent = token('--color-accent', '#007BFF');
  const onAccent = token('--color-on-accent', '#ffffff');
  const surface = token('--color-surface', '#ffffff');

  const styleCache = {};
  const clusterLayer = new ol.layer.Vector({
    source: clusters,
    style: (feature) => {
      const n = feature.get('features').length;
      if (!styleCache[n]) {
        styleCache[n] = new ol.style.Style({
          image: new ol.style.Circle({
            radius: Math.min(22, 11 + Math.sqrt(n) * 2),
            fill: new ol.style.Fill({ color: accent }),
            stroke: new ol.style.Stroke({ color: surface, width: 2 }),
          }),
          text: new ol.style.Text({
            text: n > 1 ? String(n) : '',
            font: '600 11px -apple-system, sans-serif',
            fill: new ol.style.Fill({ color: onAccent }),
          }),
        });
      }
      return styleCache[n];
    },
  });

  const map = new ol.Map({
    target: 'map',
    layers: [new ol.layer.Tile({ source: new ol.source.OSM() }), clusterLayer],
    view: new ol.View({ center: ol.proj.fromLonLat([0, 20]), zoom: 1 }),
  });
  if (features.length) {
    map.getView().fit(points.getExtent(), { padding: [28, 28, 28, 28], maxZoom: 12, duration: 0 });
  }

  // A plain popup positioned over the clicked cluster: place it at the cluster's
  // pixel and re-place it on every render so it tracks the map as it pans/zooms.
  const popup = document.getElementById('popup');
  const mainImg = document.getElementById('popup-main');
  const cap = document.getElementById('popup-cap');
  const thumbs = document.getElementById('popup-thumbs');
  let activeCoord = null;

  const caption = (p) => [p.location_name, p.year].filter(Boolean).join(' · ') || ('Photo #' + p.id);
  function showPhoto(p, thumbEl) {
    mainImg.src = yaffo.mediaUrl(p.id);
    cap.textContent = caption(p);
    thumbs.querySelectorAll('img').forEach((t) => t.classList.remove('active'));
    if (thumbEl) thumbEl.classList.add('active');
  }
  function reposition() {
    if (!activeCoord) return;
    const px = map.getPixelFromCoordinate(activeCoord);
    if (px) { popup.style.left = px[0] + 'px'; popup.style.top = px[1] + 'px'; }
  }
  function close() { activeCoord = null; popup.style.display = 'none'; }
  document.getElementById('popup-close').addEventListener('click', close);
  map.on('postrender', reposition);  // keep it anchored as the map moves

  map.on('pointermove', (e) => { map.getTargetElement().style.cursor = map.hasFeatureAtPixel(e.pixel) ? 'pointer' : ''; });
  map.on('click', (e) => {
    const feature = map.forEachFeatureAtPixel(e.pixel, (f) => f);
    if (!feature) { close(); return; }
    const pics = feature.get('features').map((f) => f.get('photo'));
    thumbs.innerHTML = '';
    pics.slice(0, 30).forEach((p, i) => {
      const t = document.createElement('img');
      t.src = yaffo.mediaUrl(p.id);
      t.loading = 'lazy';
      t.addEventListener('click', () => showPhoto(p, t));  // click a thumbnail -> show it large
      thumbs.appendChild(t);
      if (i === 0) showPhoto(p, t);  // start on the first
    });
    activeCoord = feature.getGeometry().getCoordinates();
    popup.style.display = 'block';
    reposition();
  });
})();
""",
    grid_w=12, grid_h=5,
)


# Pair 3 (topic 'folder'): a picker walks the indexed on-disk folder tree; a gallery
# shows the photos indexed at the selected folder.
_FOLDER_PICKER = WidgetTemplate(
    name="Folder picker",
    description=(
        "PUB/SUB publisher (topic 'folder'). Walks the indexed library's on-disk folder "
        "tree using the media-dir sources: a select from yaffo.query({source:'media_dirs'}) "
        "plus breadcrumbs and folder chips (with photo counts) from "
        "yaffo.query({source:'folders', media_dir_id, path}). On each navigation it "
        "yaffo.publish('folder', {mediaDirId, path}) and persists the spot. Pair it with a "
        "'Folder gallery'."
    ),
    data_query={},
    html="""\
<link rel="stylesheet" href="/static/searchable-select.css">
<div class='fb' id='root'>
  <div class='fb-bar'>
    <select id='dir' class='searchable-select' data-search-disabled></select>
    <nav class='fb-crumbs' id='crumbs'></nav>
  </div>
  <div class='fb-folders' id='folders'></div>
</div>
<script src="/static/searchable-select.js"></script>
""",
    css="""\
.fb { display: flex; flex-direction: column; gap: 10px; }
.fb-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.fb-bar .searchable-select-wrapper { width: 170px; }
/* The dropdown can't escape the widget iframe, so keep it short enough to fit. */
.fb-bar .searchable-select-options { max-height: 160px; }
.fb-crumbs { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; font-size: var(--font-size-sm); }
.fb-crumb { background: none; border: none; color: var(--color-accent); cursor: pointer; padding: 2px 6px; border-radius: var(--radius-sm); font: inherit; }
.fb-crumb:hover { background: var(--color-surface-sunken); }
.fb-sep { color: var(--color-text-faint); }
.fb-folders { display: flex; flex-wrap: wrap; gap: 8px; }
.fb-folder { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; background: var(--color-surface); border: var(--border-width) solid var(--color-border); border-radius: var(--radius-md); color: var(--color-text); cursor: pointer; font: inherit; font-size: var(--font-size-sm); }
.fb-folder:hover { border-color: var(--color-border-strong); background: var(--color-surface-sunken); }
.fb-ico { font-size: var(--font-size-md); }
.fb-count { color: var(--color-text-muted); font-size: var(--font-size-xs); }
""",
    js="""\
const dirSel = document.getElementById('dir');
const crumbs = document.getElementById('crumbs');
const foldersEl = document.getElementById('folders');

// Persisted across renders: which media dir we're in and the path within it.
const state = Object.assign({ mediaDirId: null, path: '' }, yaffo.state || {});
const opt = (value, label) => { const o = document.createElement('option'); o.value = value; o.textContent = label; return o; };

function emit() {
  yaffo.publish('folder', { mediaDirId: state.mediaDirId, path: state.path });
  yaffo.saveState({ mediaDirId: state.mediaDirId, path: state.path });
}
function go(path) { state.path = path; emit(); render(); }

function drawCrumbs() {
  crumbs.innerHTML = '';
  const home = document.createElement('button');
  home.className = 'fb-crumb'; home.textContent = '🏠'; home.title = 'Top';
  home.onclick = () => go('');
  crumbs.appendChild(home);
  let acc = '';
  (state.path ? state.path.split('/') : []).forEach((part) => {
    acc = acc ? acc + '/' + part : part;
    const target = acc;
    const sep = document.createElement('span'); sep.className = 'fb-sep'; sep.textContent = '/';
    const b = document.createElement('button'); b.className = 'fb-crumb'; b.textContent = part;
    b.onclick = () => go(target);
    crumbs.appendChild(sep); crumbs.appendChild(b);
  });
}

// folders: [{ name, media_count }] from the `folders` source.
function drawFolders(folders) {
  foldersEl.innerHTML = '';
  if (!folders.length) { foldersEl.innerHTML = "<div class='yf-empty'>No subfolders here.</div>"; return; }
  folders.forEach((f) => {
    const b = document.createElement('button');
    b.className = 'fb-folder';
    const ico = document.createElement('span'); ico.className = 'fb-ico'; ico.textContent = '📁';
    const label = document.createElement('span'); label.textContent = f.name;
    const count = document.createElement('span'); count.className = 'fb-count'; count.textContent = f.media_count;
    b.append(ico, label, count);
    b.onclick = () => go(state.path ? state.path + '/' + f.name : f.name);
    foldersEl.appendChild(b);
  });
}

async function render() {
  drawCrumbs();
  if (!state.mediaDirId) { foldersEl.innerHTML = ''; return; }
  foldersEl.innerHTML = "<div class='yf-empty'>Loading…</div>";
  const folders = await yaffo.query({ source: 'folders', media_dir_id: state.mediaDirId, path: state.path });
  if (!folders) { foldersEl.innerHTML = "<div class='yf-empty'>Couldn't read this folder.</div>"; return; }
  drawFolders(folders);
}

async function init() {
  const dirs = (await yaffo.query({ source: 'media_dirs' })) || [];
  if (!dirs.length) { document.getElementById('root').innerHTML = "<div class='yf-empty'>No media directories are configured.</div>"; return; }
  dirs.forEach((d) => dirSel.appendChild(opt(d.id, d.name)));
  // Restore the saved dir if it still exists, otherwise start at the first.
  if (!dirs.some((d) => d.id === state.mediaDirId)) { state.mediaDirId = dirs[0].id; state.path = ''; }
  dirSel.value = state.mediaDirId;
  if (window.SearchableSelect) SearchableSelect.init(dirSel);
  dirSel.onchange = () => { state.mediaDirId = dirSel.value; state.path = ''; emit(); render(); };
  emit();   // sync a gallery already on the page to the starting location
  render();
}
init();
""",
    grid_w=5, grid_h=4,
)


_FOLDER_GALLERY = WidgetTemplate(
    name="Folder gallery",
    description=(
        "PUB/SUB subscriber (topic 'folder'). A photo grid that yaffo.subscribe('folder', …) "
        "and, on each event, re-queries the photos under that folder with "
        "yaffo.query({source:'media_items', media_dir_id:{eq}, relative_path:{prefix}}). No "
        "data_query — it shows whatever the paired 'Folder picker' selects (and restores the "
        "last folder from state on reload)."
    ),
    data_query={},
    html="<div class='grid' id='root'></div>",
    css="""\
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 8px; }
.grid img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: var(--radius-md); display: block; }
""",
    js="""\
const grid = document.getElementById('root');
const st = yaffo.state || {};

function draw(rows) {
  grid.innerHTML = '';
  if (!rows || !rows.length) { grid.innerHTML = "<div class='yf-empty'>No photos in this folder.</div>"; return; }
  rows.forEach((p) => { const img = document.createElement('img'); img.src = yaffo.mediaUrl(p.id); img.loading = 'lazy'; grid.appendChild(img); });
}

async function show(loc) {
  yaffo.saveState(loc || {});
  if (!loc || !loc.mediaDirId) { grid.innerHTML = "<div class='yf-empty'>Pick a folder.</div>"; return; }
  grid.innerHTML = "<div class='yf-empty'>Loading…</div>";
  // Photos anywhere under the selected folder; relative_path needs media_dir_id to pin the root.
  const q = { source: 'media_items', media_dir_id: { eq: loc.mediaDirId }, limit: 500 };
  if (loc.path) q.relative_path = { prefix: loc.path + '/' };
  draw(await yaffo.query(q));
}

yaffo.subscribe('folder', show);
// Restore the last folder on reload; otherwise wait for the picker's first event.
if (st.mediaDirId) show(st);
else grid.innerHTML = "<div class='yf-empty'>Pick a folder.</div>";
""",
    grid_w=7, grid_h=4,
)


# The catalog, in a sensible reading order (pub/sub pairs kept adjacent so the seed
# showcase lays them out together).
TEMPLATES: list[WidgetTemplate] = [
    _HERO_BANNER,
    _PHOTO_GRID,
    _GALLERY,
    _STATS,
    _FEATURED,
    _FILTERABLE_GALLERY,
    _PEOPLE,
    _PHOTO_MAP,
    _FILTER_CONTROLS,
    _LINKED_GALLERY,
    _PHOTO_PICKER,
    _PHOTO_SPOTLIGHT,
    _FOLDER_PICKER,
    _FOLDER_GALLERY,
]

TEMPLATES_BY_NAME: dict[str, WidgetTemplate] = {t.name: t for t in TEMPLATES}
