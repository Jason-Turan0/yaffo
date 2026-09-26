// @ts-check

/**
 * The timeline view's date scrubber. The rail is a TIME axis over the filtered
 * library — newest month at the top, oldest at the bottom, every calendar month
 * an equal slice — with per-month density bars rendered server-side alongside.
 * A long empty stretch is collapsed to a short break (so one far-off date can't
 * squeeze the real library into a sliver), which makes the axis only piecewise
 * linear: the server sends each month's rail position (top/height, in %) and
 * the math below reads those rather than assuming a single linear scale.
 * Dragging or hovering shows a month/year bubble; a point in an empty month
 * snaps to the next older month that has photos (what you'd actually reach).
 * Releasing navigates to the page where that month starts — exact because the
 * gallery orders by date desc: item offset divided by page size IS the page.
 *
 * The year marks inside the rail are plain links (the no-JS fallback); with JS
 * active the whole rail is one pointer target and handled clicks are suppressed.
 */

/** @typedef {{ year: number, month: number, count: number, offset: number, top: number, height: number }} TimelineMonth */

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.media = window.PHOTO_ORGANIZER.media || {};
const timelineScrubberApi = window.PHOTO_ORGANIZER.media.timelineScrubber =
    /** @type {TimelineScrubberNamespace} */ (window.PHOTO_ORGANIZER.media.timelineScrubber || {});

/** @param {TimelineMonth} month */
const monthKey = (month) => month.year * 12 + month.month;

/**
 * The month at `fraction` of the rail (0 = top = newest). A fraction that lands
 * in an empty stretch (to scale, or a collapsed break) snaps to the next older
 * month with photos (what you'd actually reach).
 * @param {TimelineMonth[]} months newest-first, with server-computed positions
 * @param {number} fraction
 * @returns {TimelineMonth}
 */
timelineScrubberApi.monthAtFraction = (months, fraction) => {
    const percent = Math.min(Math.max(fraction, 0), 1) * 100;
    return months.find((month) => month.top + month.height > percent) || months[months.length - 1];
};

/**
 * The 1-based gallery page where this month's first (newest) item lands.
 * @param {TimelineMonth} month
 * @param {number} pageSize
 * @returns {number}
 */
timelineScrubberApi.pageForMonth = (month, pageSize) => Math.floor(month.offset / pageSize) + 1;

/**
 * Where a viewed date sits on the rail, as a 0-100 percentage (0 = newest).
 * Interpolates within the month band — later days of a month are NEWER, so day
 * 31 sits at the band's top edge. A month without photos is placed between its
 * neighbours: to scale across a short gap, proportionally across a collapsed
 * break. Returns null for a date the axis can't place (e.g. the "unknown" tail
 * marker).
 * @param {TimelineMonth[]} months newest-first, with server-computed positions
 * @param {string} isoDate "YYYY-MM-DD"
 * @returns {number | null}
 */
timelineScrubberApi.railPercentForDate = (months, isoDate) => {
    const [year, month, day] = isoDate.split('-').map(Number);
    if (!year || !month) return null;
    const key = year * 12 + month;
    const daysInMonth = new Date(year, month, 0).getDate();
    const intraMonth = day ? (daysInMonth - day) / daysInMonth : 0;
    if (key > monthKey(months[0])) return 0;
    for (let i = 0; i < months.length; i += 1) {
        const current = months[i];
        if (monthKey(current) === key) {
            return current.top + intraMonth * current.height;
        }
        const older = months[i + 1];
        if (older && monthKey(current) > key && key > monthKey(older)) {
            // The empty months strictly between the two, spread over the space
            // between the newer band's bottom and the older band's top.
            const newerBottom = current.top + current.height;
            const emptyMonths = monthKey(current) - monthKey(older) - 1;
            const monthsIn = (monthKey(current) - 1 - key) + intraMonth;
            return newerBottom + (monthsIn / emptyMonths) * (older.top - newerBottom);
        }
    }
    return 100;
};

/**
 * The jump destination for a month: its page, plus the #month anchor — the
 * month's first photo sits mid-page (the page starts at a floor'd offset), so
 * the anchor scrolls the landing to the month's divider. When only the hash
 * differs the browser scrolls without a reload.
 * @param {TimelineMonth} month
 * @param {number} pageSize
 * @param {string} href the current location
 * @returns {string}
 */
timelineScrubberApi.jumpUrl = (month, pageSize, href) => {
    const url = new URL(href);
    url.searchParams.set('view', 'timeline');
    url.searchParams.set('page', String(timelineScrubberApi.pageForMonth(month, pageSize)));
    url.hash = `month-${String(month.year).padStart(4, '0')}-${String(month.month).padStart(2, '0')}`;
    return url.toString();
};

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
    const marker = /** @type {HTMLElement | null} */ (rail.querySelector('.timeline-scrubber-marker'));
    const timeline = document.querySelector('.timeline');
    const yearLabels = rail.querySelectorAll('.timeline-scrubber-year');
    const pageSize = parseInt(rail.dataset.pageSize || '25', 10) || 25;
    const monthFormat = new Intl.DateTimeFormat(config.i18n.locale, { month: 'long', year: 'numeric' });
    let suppressClick = false;
    let pointerActive = false;

    // Evenly-spaced-by-time labels can still crowd on a short rail; keep the
    // first of any overlapping run visible and hide the rest.
    if (rail.clientHeight > 0) {
        let lastBottom = -Infinity;
        yearLabels.forEach((label) => {
            const rect = label.getBoundingClientRect();
            if (lastBottom > rect.top) {
                /** @type {HTMLElement} */ (label).dataset.crowded = 'true';
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
        pointerActive = true;
        const fraction = fractionAt(event);
        const month = timelineScrubberApi.monthAtFraction(months, fraction);
        bubble.classList.remove('is-transient');
        bubble.textContent = monthFormat.format(new Date(month.year, month.month - 1, 1));
        bubble.style.top = `${Math.min(Math.max(fraction, 0), 1) * 100}%`;
        bubble.hidden = false;
    };

    rail.addEventListener('pointermove', showBubble);
    rail.addEventListener('pointerleave', () => {
        pointerActive = false;
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
        window.location.assign(timelineScrubberApi.jumpUrl(month, pageSize, window.location.href));
    });
    // A pointerup over a year link would also fire the link's click and race our
    // navigation; swallow exactly that one. Keyboard activation never sets the flag.
    rail.addEventListener('click', (event) => {
        if (suppressClick) {
            suppressClick = false;
            event.preventDefault();
        }
    });

    bubble?.addEventListener('animationend', () => {
        if (!bubble.classList.contains('is-transient')) return;
        bubble.hidden = true;
        bubble.classList.remove('is-transient');
    });

    let updateRequested = false;
    let flashRequested = false;
    const updateViewportDate = () => {
        updateRequested = false;
        const shouldFlash = flashRequested;
        flashRequested = false;
        if (!marker || !timeline) return;
        const top = navbar instanceof HTMLElement ? navbar.getBoundingClientRect().bottom : 0;
        const sections = timeline.querySelectorAll('.timeline-section');
        const section = Array.from(sections).find((candidate) => candidate.getBoundingClientRect().bottom > top)
            || sections[sections.length - 1];
        if (!(section instanceof HTMLElement)) return;

        const date = section.dataset.date || '';
        const percent = date === 'unknown' ? 100 : timelineScrubberApi.railPercentForDate(months, date);
        if (percent === null) return;
        marker.style.top = `${percent}%`;
        if (shouldFlash && bubble && !pointerActive) {
            const [year, month] = date.split('-').map(Number);
            bubble.textContent = year && month
                ? monthFormat.format(new Date(year, month - 1, 1))
                : section.querySelector('.timeline-day-label')?.textContent?.trim() || '';
            bubble.style.top = `${percent}%`;
            bubble.hidden = false;
            bubble.classList.remove('is-transient');
            void bubble.offsetWidth;
            bubble.classList.add('is-transient');
        }
        const activeYear = date === 'unknown' ? '' : date.slice(0, 4);
        yearLabels.forEach((label) => {
            const isActive = /** @type {HTMLElement} */ (label).dataset.year === activeYear;
            label.classList.toggle('is-active', isActive);
            /** @type {HTMLElement} */ (label).style.visibility =
                isActive || /** @type {HTMLElement} */ (label).dataset.crowded !== 'true' ? 'visible' : 'hidden';
        });
    };
    /** @param {boolean} [flash] */
    const requestViewportUpdate = (flash = false) => {
        flashRequested ||= flash;
        if (updateRequested) return;
        updateRequested = true;
        window.requestAnimationFrame(updateViewportDate);
    };

    window.addEventListener('scroll', () => requestViewportUpdate(true), { passive: true });
    document.body.addEventListener('htmx:afterSwap', () => requestViewportUpdate());
    requestViewportUpdate();
};
