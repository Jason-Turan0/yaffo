# photo_gallery menu triage (2026-09-28)

Failure: gallery_menu_is_reachable_on_a_narrow_screen - `.navbar-nav .nav-link` expected 9, got 10.

Root cause: test_code_defect.
- base.html `.navbar-nav` now contains 9 destination `<a class="nav-link">` plus 1 assistant
  `<button class="nav-link nav-assistant-toggle">` ("Ask Yaffo") when assistant_ready is true.
- The utilities destination was renamed "Utilities" -> "Library".
- The spec goal (all nine primary destinations reachable) is still met; the test selector
  `.navbar-nav .nav-link` incorrectly counts the assistant button as a destination link,
  and its name list still expects the stale "Utilities" label.

Fix applied: `.navbar-nav .nav-link` -> `.navbar-nav a.nav-link` (nine destination links),
and 'Utilities' -> 'Library' in the destination name list.
