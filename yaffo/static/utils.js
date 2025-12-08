window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.utils = window.PHOTO_ORGANIZER.utils || {};

window.PHOTO_ORGANIZER.utils.initImageFallbacks = () => {
    document.querySelectorAll('img[data-fallback]').forEach((img) => {
        const applyFallback = () => {
            const fallback = img.dataset.fallback;
            if (fallback && img.src !== fallback) {
                img.src = fallback;
            }
        };

        // Check if image already failed (complete but no dimensions = error)
        if (img.complete && img.naturalWidth === 0) {
            applyFallback();
        } else {
            // Attach handler for future errors
            img.addEventListener('error', applyFallback, { once: true });
        }
    });
};

window.PHOTO_ORGANIZER.utils.date = {
    /**
     * Format an ISO date string based on the user's browser locale settings
     * @param {string} isoDate - ISO date string (e.g., "2024-03-15T10:30:00Z")
     * @param {object} options - Optional Intl.DateTimeFormat options override
     * @returns {string} - Formatted date string
     */
    format: (isoDate, options = {}) => {
        if (!isoDate) return '';
        const date = new Date(isoDate);
        if (isNaN(date.getTime())) return '';

        const defaultOptions = {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        };
        return new Intl.DateTimeFormat(undefined, { ...defaultOptions, ...options }).format(date);
    },

    /**
     * Format an ISO date string with time based on user's browser locale
     * @param {string} isoDate - ISO date string
     * @param {object} options - Optional Intl.DateTimeFormat options override
     * @returns {string} - Formatted date/time string
     */
    formatWithTime: (isoDate, options = {}) => {
        if (!isoDate) return '';
        const date = new Date(isoDate);
        if (isNaN(date.getTime())) return '';

        const defaultOptions = {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit'
        };
        return new Intl.DateTimeFormat(undefined, { ...defaultOptions, ...options }).format(date);
    },

    /**
     * Format an ISO date string as relative time (e.g., "2 days ago")
     * @param {string} isoDate - ISO date string
     * @returns {string} - Relative time string
     */
    formatRelative: (isoDate) => {
        if (!isoDate) return '';
        const date = new Date(isoDate);
        if (isNaN(date.getTime())) return '';

        const now = new Date();
        const diffMs = now - date;
        const diffSecs = Math.floor(diffMs / 1000);
        const diffMins = Math.floor(diffSecs / 60);
        const diffHours = Math.floor(diffMins / 60);
        const diffDays = Math.floor(diffHours / 24);

        const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });

        if (diffDays > 30) {
            return window.PHOTO_ORGANIZER.utils.date.format(isoDate);
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