window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

// Wires every favorite heart on the page: the detail view's single toggle and each
// home-grid card's. Buttons carry .favorite-toggle + data-photo-id; the click is
// stopped from bubbling so a card's open-photo handler doesn't also fire.
window.PHOTO_ORGANIZER.initFavoriteToggles = (config) => {
    const toggle = async (button) => {
        const photoId = button.dataset.photoId;
        button.disabled = true;
        try {
            const response = await fetch(config.buildUrl('toggle_media_favorite', { media_item_id: photoId }), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            if (!response.ok) throw new Error('Request failed');
            const data = await response.json();
            button.classList.toggle('is-favorite', !!data.favorite);
            button.setAttribute('aria-pressed', data.favorite ? 'true' : 'false');
        } catch (error) {
            notification.error('Failed to update favorite');
            console.error('Error:', error);
        } finally {
            button.disabled = false;
        }
    };

    document.querySelectorAll('.favorite-toggle[data-photo-id]').forEach((button) => {
        button.addEventListener('click', (event) => {
            event.stopPropagation();
            event.preventDefault();
            toggle(button);
        });
    });
};
