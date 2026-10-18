# User guide component mockups

Open `index.html` directly in a browser. Screenshots, styles, and example data are
embedded; no build or server is required to explore the demos. Guide links point
to the local MkDocs preview on port 8002.

The gallery contains:

1. Annotated screenshot with numbered markers and matching descriptions.
2. Library action chooser with assignment-preservation consequences.
3. Missing-photo troubleshooting decision tree with back/reset controls.
4. Interactive any/all people-filter demonstration using illustrative data.
5. Duplicate action comparison with recovery explanations and a sample checklist.
6. Draft → Test → Publish → Enable → Run lifecycle explorer.
7. Goal-based organization-method chooser.
8. Context-specific keyboard shortcut reference.
9. Keyboard-accessible operating-system tabs with shared commands.
10. Settings search with synonyms and guide-section links.
11. Expandable prompt examples, prerequisites, and approval explanations.

A bonus section applies the guided-step pattern to importing photos.

The controls explain behavior and update the mockup only. They do not call the app
or modify library data. Copy buttons copy the displayed command without running it.

To rebuild the standalone file after editing `template.html`:

```sh
python3 work/guide-components/build.py
```

To serve only this directory locally:

```sh
python3 -m http.server 8003 --bind 127.0.0.1 --directory work/guide-components
```
