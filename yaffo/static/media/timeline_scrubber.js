// @ts-check

/**
 * The timeline view's date scrubber. The rail is a TIME axis over the filtered
 * library — newest month at the top, oldest at the bottom, every calendar month
 * an equal slice — with per-month density bars rendered server-side alongside.
 * Dragging or hovering shows a month/year bubble; a point in an empty month
 * snaps to the next older month that has photos (what you'd actually reach).
 * Releasing navigates to the page where that month starts — exact because the
 * gallery orders by date desc: item offset divided by page size IS the page.
 *
 * The year marks inside the rail are plain links (the no-JS fallback); with JS
 * active the whole rail is one pointer target and handled clicks are suppressed.
 */

/** @typedef {{ year: number, month: number, count: number, offset: number }} TimelineMonth */

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.media = window.PHOTO_ORGANIZER.media || {};
const timelineScrubberApi = window.PHOTO_ORGANIZER.media.timelineScrubber =
    /** @type {TimelineScrubberNamespace} */ (window.PHOTO_ORGANIZER.media.timelineScrubber || {});

/** @param {TimelineMonth} month */
const monthKey = (month) => month.year * 12 + month.month;

/**
 * The month at `fraction` of the time axis (0 = top = newest). A fraction that
 * lands in an empty calendar month snaps to the next older month with photos.
 * @param {TimelineMonth[]} months newest-first, offsets cumulative
 * @param {number} fraction
 * @returns {TimelineMonth}
 */
timelineScrubberApi.monthAtFraction = (months, fraction) => {
    const newest = monthKey(months[0]);
    const oldest = monthKey(months[months.length - 1]);
    const totalMonths = newest - oldest + 1;
    const clamped = Math.min(Math.max(fraction, 0), 1);
    const targetKey = newest - Math.min(Math.floor(clamped * totalMonths), totalMonths - 1);
    return months.find((month) => monthKey(month) <= targetKey) || months[months.length - 1];
};

/**
 * The 1-based gallery page where this month's first (newest) item lands.
 * @param {TimelineMonth} month
 * @param {number} pageSize
 * @returns {number}
 */
timelineScrubberApi.pageForMonth = (month, pageSize) => Math.floor(month.offset / pageSize) + 1;

/**
 * @param {I18nService} i18n
 * @param {AppConfig} config
 * @returns {void}
 */
timelineScrubberApi.init = (i18n, config) => {
    // The sticky day headers offset themselves by the navbar's real height
    // (themes size it differently); publish it before the rail guard so the
    // headers are right even when no scrubber renders (e.g. only undated photos).
    const navbar = document.querySelector('.navbar');
    if (navbar instanceof HTMLElement && navbar.offsetHeight > 0) {
        document.documentElement.style.setProperty('--navbar-height', `${navbar.offsetHeight}px`);
    }

    const rail = document.getElementById('timeline-scrubber');
    const payload = document.getElementById('timeline-index');
    if (!rail || !payload) return;
    /** @type {TimelineMonth[]} */
    const months = JSON.parse(payload.textContent || '[]');
    if (!months.length) return;

    const bubble = /** @type {HTMLElement | null} */ (rail.querySelector('.timeline-scrubber-bubble'));
    const pageSize = parseInt(rail.dataset.pageSize || '25', 10) || 25;
    const monthFormat = new Intl.DateTimeFormat(config.i18n.locale, { month: 'long', year: 'numeric' });
    let suppressClick = false;

    // Evenly-spaced-by-time labels can still crowd on a short rail; keep the
    // first of any overlapping run visible and hide the rest.
    if (rail.clientHeight > 0) {
        let lastBottom = -Infinity;
        rail.querySelectorAll('.timeline-scrubber-year').forEach((label) => {
            const rect = label.getBoundingClientRect();
            if (rect.top < lastBottom) {
                /** @type {HTMLElement} */ (label).style.visibility = 'hidden';
            } else {
                lastBottom = rect.bottom;
            }
        });
    }

    /** @param {PointerEvent} event */
    const fractionAt = (event) => {
        const rect = rail.getBoundingClientRect();
        if (!rect.height) return 0;
        return (event.clientY - rect.top) / rect.height;
    };

    /** @param {PointerEvent} event */
    const showBubble = (event) => {
        if (!bubble) return;
        const fraction = fractionAt(event);
        const month = timelineScrubberApi.monthAtFraction(months, fraction);
        bubble.textContent = monthFormat.format(new Date(month.year, month.month - 1, 1));
        bubble.style.top = `${Math.min(Math.max(fraction, 0), 1) * 100}%`;
        bubble.hidden = false;
    };

    rail.addEventListener('pointermove', showBubble);
    rail.addEventListener('pointerleave', () => {
        if (bubble) bubble.hidden = true;
    });
    rail.addEventListener('pointerdown', (event) => {
        // Take over the gesture: the rail is one drag target, not a row of links.
        event.preventDefault();
        rail.setPointerCapture(event.pointerId);
        showBubble(event);
    });
    rail.addEventListener('pointerup', (event) => {
        if (!rail.hasPointerCapture(event.pointerId)) return;
        rail.releasePointerCapture(event.pointerId);
        suppressClick = true;
        const month = timelineScrubberApi.monthAtFraction(months, fractionAt(event));
        const url = new URL(window.location.href);
        url.searchParams.set('view', 'timeline');
        url.searchParams.set('page', String(timelineScrubberApi.pageForMonth(month, pageSize)));
        window.location.assign(url.toString());
    });
    // A pointerup over a year link would also fire the link's click and race our
    // navigation; swallow exactly that one. Keyboard activation never sets the flag.
    rail.addEventListener('click', (event) => {
        if (suppressClick) {
            suppressClick = false;
            event.preventDefault();
        }
    });
};
