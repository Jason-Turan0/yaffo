# Settings page notes

- The Settings screen renders (in order): Language, Units, Media Directories,
  Thumbnail Directory, AI Generation, Photo Labels, System Information.
- The Language section's control is labelled "Application language" (server-rendered
  via `{{ _("Application language") }}`; en.json key `settings.applicationLanguage` is
  "Application language"). The description in settings.md with **Application language**
  is correct for the current app.
- `settings.json` in the walkthrough dir is a stale/prior triage record that proposed
  renaming the prose to "**Interface language**". Do not follow it: the current app
  template/source still says "Application language" and would make the docs wrong.
- Page dependencies (base.css, nav.js, sidebar.css, base.html, component stylesheets)
  are global chrome; changes to them do not affect the Settings content. The single
  shot `settings-overview.webp` is clipped to `.main-content` and ignores
  `.media-dir-path`, `#current-thumbnail-dir`, `#thumbnail-size`.
