"""The widget styling baseline.

Injected into every widget frame (pages/widget_frame.html) *after* the design
tokens (static/tokens.css, with the active theme stamped on <html data-theme>)
and *before* the widget's own CSS. Every widget — AI-generated or seeded —
therefore inherits the app's themed look (surface background, body font, text
colors, the `.yf-muted` / `.yf-empty` helpers) and can override specifics in its
own CSS via the cascade. Widget CSS should reference the same var(--…) tokens
rather than raw colors so it follows the active theme.
"""

WIDGET_THEME_CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  padding: 14px;
  background: var(--color-surface);
  color: var(--color-text);
  font-family: var(--font-body);
  font-size: var(--font-size-base);
  line-height: 1.45;
}
.yf-muted { color: var(--color-text-muted); font-size: var(--font-size-xs); }
.yf-empty { color: var(--color-text-faint); font-size: var(--font-size-sm); padding: 8px 2px; }
img { background: var(--color-surface-sunken); }
"""