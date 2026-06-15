window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

// Wires the shared "New automation" modal that lives in the utilities sidebar panel
// (templates/utilities/_base.html), so it works on every utilities page. Deferred to
// DOMContentLoaded because the modal component script loads later in the body.
document.addEventListener('DOMContentLoaded', () => {
    const button = document.getElementById('new-automation-button');
    const components = window.PHOTO_ORGANIZER.COMPONENTS;
    if (!button || !components || !components.modal) return;
    const modal = components.modal.init('newAutomationModal');
    button.addEventListener('click', modal.open);
});