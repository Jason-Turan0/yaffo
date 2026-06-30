window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.utilities = window.PHOTO_ORGANIZER.utilities || {};

document.addEventListener('yaffo:app-init-complete', (event) => {
    const app = event.detail.app;
    const button = document.getElementById('new-automation-button');
    const modal = app.COMPONENTS.modal.init('newAutomationModal');
    button.addEventListener('click', modal.open);
});
