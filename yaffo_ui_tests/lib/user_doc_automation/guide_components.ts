/**
 * The guide's custom markdown components, as the page-writing agents are told about
 * them. Shared by generation and the fix turn: both own page markdown, and a page
 * written without knowing these falls back to walls of prose and bare screenshots.
 *
 * The scripts behind them live in docs/javascripts/ and are described for people in
 * yaffo_ui_tests/docs/documentation_automation.md. Each one leaves its markdown fully
 * readable when it cannot enhance it, so a malformed block degrades rather than breaks.
 */
export const GUIDE_COMPONENTS = `## Markdown components

The guide has four components beyond plain markdown. Each is an HTML wrapper with
\`markdown="1"\` around ordinary markdown, so its content stays readable without
JavaScript. Pick the one that fits the content; most sections need none.

### Annotated screenshot — the default for every screenshot

Explain a screenshot by pointing at its parts. Use it for every screenshot except a
walkthrough step's, one that shows page content rather than controls, or one too
small to mark.

\`\`\`markdown
<div class="annotated-screenshot" markdown="1">

![Library Health with its counts and status](assets/indexing-library/library-health.webp)

<div class="screenshot-annotation" markdown="1">

### Library Counts

Files on disk, imported and indexed items, and missing thumbnails.
[Read about scanning](#scan-the-library).

</div>

</div>
\`\`\`

- One image per block, then one \`screenshot-annotation\` per part: a heading and a
  sentence or two, linking to the section with the detail.
- Headings are H3, or H4 when the screenshot sits inside an H3 section. Keep them
  unique on the page; they become link targets.
- **Never write marker coordinates.** In the walkthrough, give the shot
  \`annotations\`: each annotation's heading id (\`Library Counts\` ->
  \`library-counts\`) mapped to a selector for the element it describes. Capture
  measures the elements and writes \`data-x\`, \`data-y\`, and \`data-region\` into the
  page. A selector matching several elements frames all of them.
- Add \`annotated-screenshot--narrow\` to the wrapper's class for a tall, narrow
  image such as a side panel.

### Guided walkthrough — a sequence of steps

\`\`\`markdown
<div class="guide-walkthrough" markdown="1">

### Create an Import Sync

Choose **New automation** → **Import from…** and name the sync.

![The new automation dialog with Import from selected](assets/importing/new-import-sync.webp)

### Choose What to Import

...
</div>
\`\`\`

Every H3 is a step with **exactly one** image; a step without one leaves the whole
block as plain markdown. Use it for a flow the reader follows in order, with a
screenshot per step. Content that is not a step goes outside the wrapper.

### Action chooser — pick one of several options

\`\`\`markdown
<div class="action-chooser" data-initial="none" markdown="1">
<div class="action-choice" data-choice="Photos are missing" markdown="1">

### Photos Do Not Appear

...
</div>
<div class="action-choice" data-choice="Jobs are stuck" markdown="1">

### Jobs Are Stuck or Slow

...
</div>
</div>
\`\`\`

Each choice starts with an H3; \`data-choice\` is its button label. Only the chosen
section shows. Use it when readers need one of several parallel sections: what to
do, which tool fits, which symptom they have. \`data-initial="none"\` starts with
nothing shown; \`data-status="Checks for: {choice}"\` adds a line under the buttons.

### Troubleshooting navigator — a yes/no diagnosis

\`\`\`markdown
<div class="troubleshooting-navigator" markdown="1">
<div class="navigator-step" markdown="1">

#### Do Photos Appear After Clearing Filters?

- [Yes, now I can see them](#reapply-filters-one-at-a-time)
- [No, they are still missing](#is-the-folder-listed-in-media-directories)

</div>
<div class="navigator-step" markdown="1">

**Reapply Filters One at a Time**
{: #reapply-filters-one-at-a-time .navigator-outcome }

...
</div>
</div>
\`\`\`

Questions are headings (H3, or H4 inside an H3 section); outcomes are bold
paragraphs with an id and \`.navigator-outcome\`, which keeps them out of the table
of contents. Answer links that target a step become buttons; every same-page link
must reach a step or a heading that exists, or the block stays static. The first
step is the starting question; order the questions cheapest check first.`;
