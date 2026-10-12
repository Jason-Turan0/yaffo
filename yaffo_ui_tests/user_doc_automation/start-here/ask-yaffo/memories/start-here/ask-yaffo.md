# ask-yaffo

Last triage (ask-yaffo-conversation.webp): canvas height grew 1782 -> 1854 device px
(+36 CSS px) after chrome diffs (templates/base.html, static/nav.js initNavPagesBar
signature, static/app.js). Whole conversation (list, header, messages, sources,
expanded script activity, Open link) is pixel-identical; only the bottom-anchored
composer + Send/Cancel shift down uniformly.

=> The .assistant-page clip is bottom-anchored inside a container sized off the page
chrome, so any nav/pages-bar height change moves the composer and changes the shot's
height without changing content. Treat such pure vertical shifts as layout, not as a
walkthrough or app defect; nothing in the prose depends on the composer's position.

Fixture facts used by the shots: conversations 901 (HELP, "Photo folders and Maya at
the ..." / "8 of the 14"), 902 (album/plan), 900 (OLDER); second
details.assistant-activity is the library-question script that the shot expands.
No provider key in the sandbox: all assistant endpoints are simulated.
