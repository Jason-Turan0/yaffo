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
