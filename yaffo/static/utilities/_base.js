window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

window.PHOTO_ORGANIZER.initUtilitiesBase = () => {
    document.addEventListener('DOMContentLoaded', () => {
        const button = document.getElementById('new-automation-button');
        const components = window.PHOTO_ORGANIZER.COMPONENTS;
        if (!button || !components || !components.modal) return;
        const modal = components.modal.init('newAutomationModal');
        button.addEventListener('click', modal.open);
    });
};

window.PHOTO_ORGANIZER.initUtilitiesBase();
