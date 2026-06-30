// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.utilities = window.PHOTO_ORGANIZER.utilities || {};

window.PHOTO_ORGANIZER.utilities.initRemoveDuplicates = () => {
    document.addEventListener('htmx:afterRequest', (event) => {
        if (!('detail' in event)) return;
        const detail = /** @type {{ elt?: Element, successful?: boolean }} */ (event.detail);
        if (detail.elt?.id === 'find-duplicates-button' && detail.successful) {
            window.location.reload();
        }
    });
};
