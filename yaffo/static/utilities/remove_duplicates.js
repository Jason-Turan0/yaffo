window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

window.PHOTO_ORGANIZER.initRemoveDuplicates = () => {
    document.addEventListener('htmx:afterRequest', (event) => {
        if (event.detail.elt.id === 'find-duplicates-button' && event.detail.successful) {
            window.location.reload();
        }
    });
};
