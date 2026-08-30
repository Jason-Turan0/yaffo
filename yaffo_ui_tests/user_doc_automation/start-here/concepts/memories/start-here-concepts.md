# start-here/concepts triage notes

- Page owns no screenshots; a non-visual dependency-content check flags it when
  observed static/template JS changes.
- The glossary's `also_depends_on` is yaffo/db/models.py. In a dependency
  change triage, check models.py for a NEW first-class entity: if models.py is
  not among the changed files, there is no new model concept, so the glossary
  usually needs no change.
- Glossary covers: library, media item, media directory, index/indexed,
  thumbnail, orphaned item, tag, label, person, face, location name, favorite,
  album, background job, duplicate group, automation, custom page, widget, theme.
- "Sharing" (KnownDevice/ShareGrant) and "life stage" (PersonEmbedding) exist in
  the model but are NOT glossary terms; the guide does not use "sharing" as an
  undefined term, so do not add it to the glossary on a routine dependency bump.
- The walkthrough visits /albums, /people, /faces?group_by=people&threshold=100,
  /locations, /utilities/automations, /themes, /utilities/index-photos,
  /jobs/section, /media/view/:id, and the seeded "Florida Trip" custom page.
  It does not visit /sharing.
