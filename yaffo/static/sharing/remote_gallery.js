// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.sharing = window.PHOTO_ORGANIZER.sharing || {};
const remoteGallery = window.PHOTO_ORGANIZER.sharing.remoteGallery =
    window.PHOTO_ORGANIZER.sharing.remoteGallery || {};

// Every preview on the remote gallery is a live p2p call to the peer, so the
// browser must NOT fire them all at once (native lazy-loading easily has
// dozens in flight — enough to trip call timeouts and the hub's per-device
// relay-session cap, which surfaced as 502s). Instead the grid renders
// placeholders with data-preview-src, and this module loads them through a
// small queue: viewport-first via IntersectionObserver, a strict concurrency
// limit, and one delayed retry for transient failures before falling back to
// the placeholder for good.
const MAX_CONCURRENT_LOADS = 4;
const RETRY_DELAY_MS = 2000;

remoteGallery.init = () => {
    /** @type {HTMLImageElement[]} */
    const queue = [];
    let inFlight = 0;

    /** @param {HTMLImageElement} img */
    const finish = (img) => {
        inFlight -= 1;
        img.dataset.previewState = 'done';
        img.classList.remove('preview-pending');
        pump();
    };

    /** @param {HTMLImageElement} img */
    const load = (img) => {
        inFlight += 1;
        img.dataset.previewState = 'loading';
        // The initial src is a transparent pixel — the CSS skeleton plate
        // shows through it while the preview is pending.
        const pendingSrc = img.src;
        const onLoad = () => {
            cleanup();
            finish(img);
        };
        const onError = () => {
            cleanup();
            if (!img.dataset.previewRetried) {
                // Transient failures (a timed-out relay call) usually succeed
                // on a second, less-contended attempt.
                img.dataset.previewRetried = 'true';
                img.src = pendingSrc;
                inFlight -= 1;
                pump();  // the freed slot serves the queue while this retry waits
                setTimeout(() => {
                    queue.unshift(img);
                    pump();
                }, RETRY_DELAY_MS);
                return;
            }
            // Genuinely failed — now the "not found" placeholder is accurate.
            img.src = img.dataset.fallbackSrc || pendingSrc;
            finish(img);
        };
        const cleanup = () => {
            img.removeEventListener('load', onLoad);
            img.removeEventListener('error', onError);
        };
        img.addEventListener('load', onLoad);
        img.addEventListener('error', onError);
        img.src = /** @type {string} */ (img.dataset.previewSrc);
    };

    const pump = () => {
        while (inFlight < MAX_CONCURRENT_LOADS && queue.length > 0) {
            load(/** @type {HTMLImageElement} */ (queue.shift()));
        }
    };

    /** @param {HTMLImageElement} img */
    const enqueue = (img) => {
        if (img.dataset.previewState) return;
        img.dataset.previewState = 'queued';
        queue.push(img);
        pump();
    };

    const images = /** @type {HTMLImageElement[]} */ (
        Array.from(document.querySelectorAll('img[data-preview-src]'))
    );
    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    observer.unobserve(entry.target);
                    enqueue(/** @type {HTMLImageElement} */ (entry.target));
                }
            });
        }, { rootMargin: '300px' });
        images.forEach((img) => observer.observe(img));
    } else {
        images.forEach(enqueue);
    }
};
