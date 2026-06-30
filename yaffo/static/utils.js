// @ts-check

/**
 * @typedef {Object} DateUtils
 * @property {(isoDate: string | null | undefined, options?: Intl.DateTimeFormatOptions) => string} format
 * @property {(isoDate: string | null | undefined, options?: Intl.DateTimeFormatOptions) => string} formatWithTime
 * @property {(isoDate: string | null | undefined) => string} formatRelative
 *
 * @typedef {Object} UtilsNamespace
 * @property {string} locale
 * @property {() => void} initImageFallbacks
 * @property {DateUtils} date
 */

const utilsWindow = /** @type {Window & {
    PHOTO_ORGANIZER: { utils?: Partial<UtilsNamespace> },
    APP_CONFIG: { i18n: { locale: string } },
}} */ (/** @type {unknown} */ (window));

utilsWindow.PHOTO_ORGANIZER = utilsWindow.PHOTO_ORGANIZER || {};
const utils = /** @type {Partial<UtilsNamespace> & { locale: string }} */ (
    utilsWindow.PHOTO_ORGANIZER.utils || {}
);
utilsWindow.PHOTO_ORGANIZER.utils = utils;
utils.locale = utilsWindow.APP_CONFIG.i18n.locale;

utils.initImageFallbacks = () => {
    document.querySelectorAll('img[data-fallback]').forEach((img) => {
        const image = /** @type {HTMLImageElement} */ (img);
        const applyFallback = () => {
            const fallback = image.dataset.fallback;
            if (fallback && image.src !== fallback) {
                image.src = fallback;
            }
        };

        // Check if image already failed (complete but no dimensions = error)
        if (image.complete && image.naturalWidth === 0) {
            applyFallback();
        } else {
            // Attach handler for future errors
            image.addEventListener('error', applyFallback, { once: true });
        }
    });
};

/** @type {DateUtils} */
const dateUtils = {
    /**
     * Format an ISO date string using the selected application locale.
     * @param {string | null | undefined} isoDate - ISO date string (e.g., "2024-03-15T10:30:00Z")
     * @param {Intl.DateTimeFormatOptions} options - Optional Intl.DateTimeFormat options override
     * @returns {string} - Formatted date string
     */
    format: (isoDate, options = {}) => {
        if (!isoDate) return '';
        const date = new Date(isoDate);
        if (isNaN(date.getTime())) return '';

        /** @type {Intl.DateTimeFormatOptions} */
        const defaultOptions = {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        };
        return new Intl.DateTimeFormat(
            utils.locale,
            { ...defaultOptions, ...options }
        ).format(date);
    },

    /**
     * Format an ISO date string with time using the selected application locale.
     * @param {string | null | undefined} isoDate - ISO date string
     * @param {Intl.DateTimeFormatOptions} options - Optional Intl.DateTimeFormat options override
     * @returns {string} - Formatted date/time string
     */
    formatWithTime: (isoDate, options = {}) => {
        if (!isoDate) return '';
        const date = new Date(isoDate);
        if (isNaN(date.getTime())) return '';

        /** @type {Intl.DateTimeFormatOptions} */
        const defaultOptions = {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit'
        };
        return new Intl.DateTimeFormat(
            utils.locale,
            { ...defaultOptions, ...options }
        ).format(date);
    },

    /**
     * Format an ISO date string as relative time (e.g., "2 days ago")
     * @param {string | null | undefined} isoDate - ISO date string
     * @returns {string} - Relative time string
     */
    formatRelative: (isoDate) => {
        if (!isoDate) return '';
        const date = new Date(isoDate);
        if (isNaN(date.getTime())) return '';

        const now = new Date();
        const diffMs = now.getTime() - date.getTime();
        const diffSecs = Math.floor(diffMs / 1000);
        const diffMins = Math.floor(diffSecs / 60);
        const diffHours = Math.floor(diffMins / 60);
        const diffDays = Math.floor(diffHours / 24);

        const rtf = new Intl.RelativeTimeFormat(
            utils.locale,
            { numeric: 'auto' }
        );

        if (diffDays > 30) {
            return dateUtils.format(isoDate);
        } else if (diffDays >= 1) {
            return rtf.format(-diffDays, 'day');
        } else if (diffHours >= 1) {
            return rtf.format(-diffHours, 'hour');
        } else if (diffMins >= 1) {
            return rtf.format(-diffMins, 'minute');
        } else {
            return rtf.format(-diffSecs, 'second');
        }
    }
};

utils.date = dateUtils;
