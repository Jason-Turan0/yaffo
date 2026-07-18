// @ts-check

/**
 * Infinite scroll for the timeline view. htmx does the fetching (the sentinel
 * in _timeline_sections.html swaps itself for the next batch); this module
 * handles what a bare swap can't:
 *
 * - A batch whose first section continues the previous batch's day arrives
 *   marked .is-continuation with no header — its cards are merged into the
 *   section above so the day reads as one group, and that header's item count
 *   is updated to the merged total.
 * - Cards wire their behavior (favorite hearts, inline video play, image
 *   fallbacks) at page init; each new batch re-runs those initializers, which
 *   are idempotent and skip already-wired nodes.
 */

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.media = window.PHOTO_ORGANIZER.media || {};
const timelineStreamApi = window.PHOTO_ORGANIZER.media.timelineStream =
    /** @type {TimelineStreamNamespace} */ (window.PHOTO_ORGANIZER.media.timelineStream || {});

/**
 * Fold every .is-continuation section into the same-day section before it.
 * @param {Element} timeline
 * @param {I18nService} i18n
 * @returns {void}
 */
timelineStreamApi.mergeContinuations = (timeline, i18n) => {
    timeline.querySelectorAll('.timeline-section.is-continuation').forEach((section) => {
        const previous = section.previousElementSibling;
        if (!previous
            || !previous.classList.contains('timeline-section')
            || previous.getAttribute('data-date') !== section.getAttribute('data-date')) {
            // Nothing to merge into (e.g. a reflowed DOM); keep the section,
            // headerless, rather than dropping photos.
            section.classList.remove('is-continuation');
            return;
        }
        const targetGrid = previous.querySelector('.photo-grid');
        const sourceGrid = section.querySelector('.photo-grid');
        if (!targetGrid || !sourceGrid) return;
        while (sourceGrid.firstChild) targetGrid.appendChild(sourceGrid.firstChild);
        section.remove();
        const count = targetGrid.querySelectorAll('.photo-card').length;
        const countLabel = previous.querySelector('.timeline-day-count');
        if (countLabel) countLabel.textContent = i18n.t('media:timeline.itemCount', { count });
    });
};

/**
 * @param {I18nService} i18n
 * @param {AppConfig} config
 * @returns {void}
 */
timelineStreamApi.init = (i18n, config) => {
    const timeline = document.querySelector('.timeline');
    if (!timeline) return;
    document.body.addEventListener('htmx:afterSwap', () => {
        timelineStreamApi.mergeContinuations(timeline, i18n);
        const media = window.PHOTO_ORGANIZER.media;
        window.PHOTO_ORGANIZER.utils?.initImageFallbacks?.();
        media?.favorite?.init?.(i18n, config);
        media?.initGalleryVideos?.(i18n, config);
    });
};
