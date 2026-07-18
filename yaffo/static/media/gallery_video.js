// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.media = window.PHOTO_ORGANIZER.media || {};

// Home-grid video previews: clicking a card's ▶ badge swaps the placeholder still
// for an inline <video> and plays it in place, instead of letting the click fall
// through to the card's open-the-view-screen navigation. Clicking anywhere else on
// the card still opens the view screen.
/**
 * @param {I18nService} i18n
 * @param {AppConfig} config
 */
window.PHOTO_ORGANIZER.media.initGalleryVideos = (i18n, config) => {
    /**
     * @param {HTMLButtonElement} badge
     */
    const playInline = (badge) => {
        const thumb = badge.closest('.photo-thumb');
        if (!thumb || thumb.querySelector('video')) return;

        const src = config.buildUrl('media', { media_item_id: badge.dataset.photoId });
        if (!src) return;

        const video = document.createElement('video');
        video.src = src;
        video.controls = true;
        video.autoplay = true;
        // Browsers block autoplay with sound; muted + playsinline lets the preview
        // start on its own (the native control bar can unmute).
        video.muted = true;
        video.playsInline = true;
        video.className = 'video-inline';
        // Player interactions (play/pause/scrub) must not bubble to the card's
        // navigation handler, or scrubbing would yank you to the view screen.
        video.addEventListener('click', (e) => e.stopPropagation());

        const still = thumb.querySelector('img');
        if (still) still.style.display = 'none';
        const duration = thumb.querySelector('.video-duration');
        if (duration && !(duration instanceof HTMLElement)) return;
        if (duration) duration.style.display = 'none';
        badge.style.display = 'none';
        // Drops the hover-info overlay so it stops intercepting the mouse-over the
        // native control bar needs (see .is-playing in index.css).
        thumb.classList.add('is-playing');

        // If the source can't load (unplayable codec — e.g. HEVC in Chrome — or the
        // file was moved/deleted), restore the card to its poster + badge instead of
        // leaving a broken player.
        video.addEventListener('error', () => {
            video.remove();
            if (still) still.style.display = '';
            if (duration) duration.style.display = '';
            badge.style.display = '';
            thumb.classList.remove('is-playing');
            window.notification.error(i18n.t('media:gallery.videoPlaybackFailed'));
        });

        thumb.appendChild(video);
    };

    // Only the interactive <button> badges (playable videos) get inline play; the
    // static span badge on non-playable formats falls through to the card's
    // open-the-detail-view click.
    document.querySelectorAll('button.video-play-badge').forEach((badge) => {
        if (!(badge instanceof HTMLButtonElement)) return;
        // Idempotent: the timeline re-runs init as batches stream in.
        if (badge.dataset.playWired) return;
        badge.dataset.playWired = '1';
        badge.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            playInline(badge);
        });
    });
};
