# Icons & themed icon buttons

How glyph icons work across themes, and the convention for adding one.

## The two-variant rule

Every icon ships in **two forms**:

1. **One outline SVG, shared by all themes.** A thin line-art glyph in
   `static/themes/classic/icon-<name>.svg`. It is drawn as a CSS **mask** filled
   with `currentColor`, so it tints to whatever the control's text color is — under
   *every* theme, including runtime (AI-generated) custom themes, which get the icon
   for free without shipping any art.

2. **One punchy override for neobrutalist.** A bold, *colored* glyph (filled shapes
   + thick `#121212` strokes) inlined as a `background-image` data-URI in
   `static/themes/neobrutalist.css`. Neobrutalist unsets the mask (see the
   `[data-theme="neobrutalist"] [data-icon]::before` reset) and shows this art
   instead, matching its sticker aesthetic.

No other theme needs icon art — they all ride the shared outline in their own
accent/text color.

## Markup

Put `data-icon="<name>"` on a `.nav-link`, `.btn`, or `button`. The glyph renders
in a `::before`; the element keeps its own text/label:

```html
<button type="button" data-icon="delete" aria-label="Remove"></button>
<a class="nav-link" data-icon="library">Home</a>
```

The registry of names → outline files lives in `static/components/icons.css`. The
neobrutalist art for each name lives in `static/themes/neobrutalist.css`.

## Adding a new icon

1. **Outline SVG** → `static/themes/classic/icon-<name>.svg`. Use
   `viewBox="0 0 24 24"`, `fill="none"`, `stroke="#000"` (the color is irrelevant —
   only the shape's alpha is used by the mask). Match the existing line weight
   (`stroke-width="2"`).
2. **Register it** in `static/components/icons.css`:
   ```css
   [data-icon="<name>"]::before { -webkit-mask-image: url("/static/themes/classic/icon-<name>.svg"); mask-image: url("/static/themes/classic/icon-<name>.svg"); }
   ```
3. **Neobrutalist art** in `static/themes/neobrutalist.css`:
   ```css
   [data-theme="neobrutalist"] [data-icon="<name>"]::before { background-image: url("data:image/svg+xml,<svg …colored, %23121212 strokes…>"); }
   ```

### Drift-guard gotchas (`tests/yaffo/test_design_tokens.py`)

The guard rejects raw colors in stylesheets, which the neobrutalist data-URIs would
trip. Two rules that keep them passing:

- **Encode `#` as `%23`** in the inline SVG (`%23ff70a6`, `%23121212`) — a literal
  `#abc` reads as a hex color and fails.
- **Don't write the words `white` / `black`** anywhere in the CSS (even in comments)
  — they're matched as named colors.

## Two-state icons

An icon that toggles (e.g. the favorite heart) ships **both** an outline and a
filled SVG and swaps the mask on a state class. See `static/components/favorite-toggle.css`
(`.is-favorite::before` points at `icon-heart-filled.svg`) and the matching
neobrutalist overrides (hollow vs hot-pink fill).

## Icon-button sticker (neobrutalist)

Bare icon buttons that float over content (the heart over a photo, the filters
"Configure" gear, the tag-row delete) get a shared **sticker** in neobrutalist: a
surface fill with a 2px border and the theme's hard offset shadow, lifting on hover.
The rule is a single grouped selector in `neobrutalist.css` — add a button's class
to that list rather than restating the border/shadow:

```css
[data-theme="neobrutalist"] .favorite-toggle,
[data-theme="neobrutalist"] .filter-config-trigger,
[data-theme="neobrutalist"] .btn-icon-delete { /* sticker */ }
```