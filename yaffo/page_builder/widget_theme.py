"""The widget styling baseline.

Injected into every widget frame (pages/widget_frame.html) *before* the widget's own
CSS, so every widget — AI-generated or seeded — inherits the app's look (system font,
white surface, `#212529` text, the `.yf-muted` / `.yf-empty` helpers) and can override
specifics in its own CSS via the cascade. Keeping it here (one constant) means the
baseline is consistent without each widget having to restate it.

Mirrors the tokens in static/base.css.
"""

WIDGET_THEME_CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  padding: 14px;
  background: #fff;
  color: #212529;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
  font-size: 14px;
  line-height: 1.45;
}
.yf-muted { color: #6c757d; font-size: 12px; }
.yf-empty { color: #adb5bd; font-size: 13px; padding: 8px 2px; }
img { background: #f1f3f5; }
"""
