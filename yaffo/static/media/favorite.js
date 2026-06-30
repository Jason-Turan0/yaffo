// @ts-check

/**
 * @typedef {Object} I18nService
 * @property {(key: string, options?: Record<string, unknown>) => string} t
 *
 * @typedef {Object} AppConfig
 * @property {(endpoint: string, params?: Record<string, string | number | undefined>) => string} buildUrl
 *
 * @typedef {Object} FavoriteResponse
 * @property {boolean} favorite
 */

const favoriteWindow = /** @type {Window & {
    PHOTO_ORGANIZER: {
        initFavoriteToggles?: (i18n: I18nService, config: AppConfig) => void,
    },
    notification: {
        error: (message: string) => void,
    },
}} */ (/** @type {unknown} */ (window));

favoriteWindow.PHOTO_ORGANIZER = favoriteWindow.PHOTO_ORGANIZER || {};

// Wires every favorite heart on the page: the detail view's single toggle and each
// home-grid card's. Buttons carry .favorite-toggle + data-photo-id; the click is
// stopped from bubbling so a card's open-photo handler doesn't also fire.
/**
 * @param {I18nService} i18n
 * @param {AppConfig} config
 * @returns {void}
 */
favoriteWindow.PHOTO_ORGANIZER.initFavoriteToggles = (i18n, config) => {
    /**
     * @param {HTMLButtonElement} button
     * @returns {Promise<void>}
     */
    const toggle = async (button) => {
        const photoId = button.dataset.photoId;
        button.disabled = true;
        try {
            const response = await fetch(config.buildUrl('toggle_media_favorite', { media_item_id: photoId }), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            if (!response.ok) throw new Error('Request failed');
            const data = /** @type {FavoriteResponse} */ (await response.json());
            button.classList.toggle('is-favorite', !!data.favorite);
            button.setAttribute('aria-pressed', data.favorite ? 'true' : 'false');
        } catch (error) {
            favoriteWindow.notification.error(i18n.t('media:favorite.updateFailed'));
            console.error('Error:', error);
        } finally {
            button.disabled = false;
        }
    };

    document.querySelectorAll('.favorite-toggle[data-photo-id]').forEach((button) => {
        const favoriteButton = /** @type {HTMLButtonElement} */ (button);
        button.addEventListener('click', (event) => {
            event.stopPropagation();
            event.preventDefault();
            toggle(favoriteButton);
        });
    });
};
