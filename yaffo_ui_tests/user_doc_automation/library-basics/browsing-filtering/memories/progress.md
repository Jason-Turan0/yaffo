# browsing-filtering

Investigation notes for this page's walkthrough. Written by the agent as it learns
things worth not rediscovering; the same role `generated_tests/*/memories/` plays.

## Known

- `?view=grid` must be pinned: the view is persisted server-side and the timeline
  scrubber rewrites it, so an unpinned `/` inherits whatever ran last.
- `year=2021` is a fixture workaround, not an editorial choice. See the walkthrough.
- Sidebar selects are custom widgets; `selectOption` does not drive them.
- Person chips reflow card heights, so a seed that assigns faces differently shifts the
  whole grid and produces a large spurious diff.

- `gallery-home.webp`'s only volatile region is the header's Grid | Timeline
  toggle. `index.css` centres the action group (`.photo-gallery .page-header-actions
  { align-self: center }`) inside a header that is `align-items: flex-start`, so the
  toggle sits ~13px lower than in pre-centring captures. That shift is benign: the
  image prose never describes the toggle, so it needs no follow-up.
- A pure translation of a shot element shows up as a diff of ~`2 * d * L` pixels
  inside a box of its own size, which is enough to tell a move from a resize or a
  colour change without opening the images.