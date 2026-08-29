# Custom Pages walkthrough notes

- The Delete Page flow must not click `#delete-page-button` immediately after
  navigating to the design page. `initDetail` wires the confirm handler only on
  `yaffo:app-init-complete`; clicking too early swallows the click and the
  `#global-confirm-dialog.active` wait times out. Wait for
  `window.PHOTO_ORGANIZER?.pages?.detail?.confirmDelete` (and the `/design` URL)
  before clicking Delete Page.
