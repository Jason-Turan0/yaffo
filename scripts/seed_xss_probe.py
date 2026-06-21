#!/usr/bin/env python3
"""Seed an 'XSS probe' page: tests the two XSS boundaries of the widget system.

A defensive security test, companion to seed_sandbox_probe. It covers:

1. **Sandbox escape** — the probe widget's JS tries to reach the parent/app-origin
   DOM, cookies, and storage. The iframe is sandboxed without `allow-same-origin`
   (a null origin), so every cross-origin access should throw SecurityError. The
   widget reports the outcome.

2. **Stored-XSS escaping** — the page subtitle, the widget's title, and a seeded
   conversation message each contain an `<img onerror>` payload. When the parent app
   renders those fields (Jinja auto-escaping), they should appear as inert text, not
   execute. If a payload fired it would set the browser tab title to `XSS-<FIELD>`;
   the widget tells you what to look for.

Nothing here is malicious: payloads only set document.title to a marker, so a failure
is loud but harmless. NOT a curated template. Run:
    python -m yaffo.scripts.seed_xss_probe

Idempotent: re-running replaces the 'XSS probe' page.
"""
from __future__ import annotations

from yaffo.app import create_app
from yaffo.db import db
from yaffo.db.repositories import custom_page_repository as page_repo

_TITLE = "XSS probe"


def _payload(marker: str) -> str:
    """An XSS attempt that, if it executes in the parent, sets the tab title to a
    field-specific marker (so a failure is visible and attributable)."""
    return "<img src=x onerror=\"document.title='XSS-%s'\">" % marker


_SUBTITLE = "Security test. This subtitle is a payload: " + _payload("SUBTITLE")
_WIDGET_TITLE = "Escape probe " + _payload("TITLE")
_MESSAGE = "Assistant turn carrying a payload: " + _payload("MESSAGE")

_CSS = """\
.probe { font-size: 13px; color: #212529; }
.probe-head strong { display: block; font-size: 14px; }
.probe-sub { color: #6c757d; font-size: 12px; margin-bottom: 10px; display: block; }
table { width: 100%; border-collapse: collapse; margin: 6px 0 14px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #dee2e6; }
th { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: #6c757d; }
td.target { font-family: 'Courier New', monospace; font-size: 12px; }
td .detail { color: #adb5bd; font-size: 11px; }
.blocked { color: #2b8a3e; font-weight: 600; }
.leaked { color: #c92a2a; font-weight: 600; }
.note { background: #fff8e1; border: 1px solid #ffe08a; border-radius: 6px; padding: 8px 10px; font-size: 12px; color: #664d03; }
.note code { font-family: 'Courier New', monospace; }
"""

_HTML = """\
<div class="probe">
  <div class="probe-head">
    <strong>🛡 XSS / sandbox-escape probe</strong>
    <span class="probe-sub">Widget JS trying to reach the parent app origin. Each should be blocked by the null-origin sandbox.</span>
  </div>
  <table>
    <thead><tr><th>Escape attempt</th><th>Result</th></tr></thead>
    <tbody id="results"></tbody>
  </table>
  <div class="note">
    <strong>Stored-XSS check (verify by eye):</strong> this widget's title, the page subtitle,
    and a chat message each contain an <code>&lt;img onerror&gt;</code> payload. Open this page in
    presentation <em>and</em> design — they should render as <em>literal text</em>. If the browser
    tab title changes to <code>XSS-TITLE</code> / <code>XSS-SUBTITLE</code> / <code>XSS-MESSAGE</code>,
    the parent failed to escape that field.
  </div>
</div>
"""

_JS = """\
(function () {
  const tbody = document.getElementById('results');
  function record(target, blocked, detail) {
    const tr = document.createElement('tr');
    tr.innerHTML = "<td class='target'></td><td></td>";
    tr.children[0].textContent = target;
    tr.children[1].innerHTML = blocked
      ? "<span class='blocked'>✓ blocked</span> <span class='detail'></span>"
      : "<span class='leaked'>✗ ESCAPED</span> <span class='detail'></span>";
    tr.children[1].querySelector('.detail').textContent = detail || '';
    tbody.appendChild(tr);
  }
  // Reaching the value => the sandbox was escaped (bad). A SecurityError => blocked (good).
  function probe(target, fn) {
    try {
      const value = fn();
      record(target, false, 'read: ' + String(value).slice(0, 50));
    } catch (err) {
      record(target, true, (err.name || 'Error') + ': ' + (err.message || '').slice(0, 70));
    }
  }

  probe('window.parent.document', () => window.parent.document.body.tagName);
  probe('window.top.document.cookie', () => window.top.document.cookie);
  probe('window.parent.location.href', () => window.parent.location.href);
  probe('document.cookie (own)', () => document.cookie);
  probe('localStorage', () => localStorage.length);
  probe('sessionStorage', () => sessionStorage.length);

  // parent.postMessage is the ONE sanctioned channel (the widget broker) — allowed,
  // but it conveys no DOM/cookie access, so it isn't an escape.
  record('window.parent.postMessage (broker)', true, 'allowed by design — message-only, no DOM access');
})();
"""


def seed_xss_probe() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()
        existing = {p.title: p.id for p in page_repo.list_pages(db.session)}
        if _TITLE in existing:
            page_repo.delete_page(db.session, existing[_TITLE])
        page = page_repo.create_page(db.session, title=_TITLE, subtitle=_SUBTITLE)
        page_repo.save_page_widgets(db.session, page.id, [{
            "id": page_repo.new_widget_id(),
            "title": _WIDGET_TITLE,
            "data_query": {},
            "html": _HTML,
            "css": _CSS,
            "js": _JS,
            "state": {},
            "x": 0, "y": 0, "w": 12, "h": 5,
        }])
        page_repo.add_message(db.session, page.id, "assistant", _MESSAGE)
        print(f"Seeded '{page.title}' (id={page.id}).")


if __name__ == "__main__":
    seed_xss_probe()
