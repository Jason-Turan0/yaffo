// @ts-check

// Notification component - reusable across the application

// A flash queued for the *next* page load (e.g. just before a reload), so a
// confirmation survives the navigation that would otherwise wipe the toast.
const NOTIFICATION_FLASH_KEY = 'app-notification-flash';

class AppNotification {
    constructor() {
        // Create notification element if it doesn't exist
        if (!document.getElementById('app-notification')) {
            this.element = document.createElement('div');
            this.element.id = 'app-notification';
            this.element.className = 'notification';
            document.body.appendChild(this.element);
        } else {
            this.element = /** @type {HTMLElement} */ (
                document.getElementById('app-notification')
            );
        }

        /** @type {ReturnType<typeof setTimeout> | null} */
        this.hideTimeout = null;
        this.showPendingFlash();
    }

    /**
     * Show a notification message
     * @param {string} message - The message to display
     * @param {NotificationType} type - The notification type
     * @param {number} duration - Duration in milliseconds (default: 3000)
     */
    show(message, type = 'success', duration = 3000) {
        // Clear any existing timeout
        if (this.hideTimeout) {
            clearTimeout(this.hideTimeout);
        }

        // Set message and type
        this.element.textContent = message;
        this.element.className = `notification ${type} visible`;

        // Auto-hide after duration
        if (duration > 0) {
            this.hideTimeout = setTimeout(() => {
                this.hide();
            }, duration);
        }
    }

    /**
     * Hide the notification
     */
    hide() {
        this.element.classList.remove('visible');
        if (this.hideTimeout) {
            clearTimeout(this.hideTimeout);
            this.hideTimeout = null;
        }
    }

    /**
     * Queue a notification to show after the next page load. Use this instead of
     * show() right before a window.location.reload()/navigation, which would
     * otherwise destroy the toast before it's seen.
     * @param {string} message
     * @param {NotificationType} type
     * @param {number} duration
     */
    flash(message, type = 'success', duration = 3000) {
        try {
            sessionStorage.setItem(NOTIFICATION_FLASH_KEY, JSON.stringify({ message, type, duration }));
        } catch {
            // sessionStorage unavailable — fall back to an immediate toast.
            this.show(message, type, duration);
        }
    }

    /** Show and clear any flash queued by a prior flash() call. */
    showPendingFlash() {
        /** @type {string | null} */
        let raw;
        try {
            raw = sessionStorage.getItem(NOTIFICATION_FLASH_KEY);
            if (raw) sessionStorage.removeItem(NOTIFICATION_FLASH_KEY);
        } catch {
            return;
        }
        if (!raw) return;
        try {
            const { message, type, duration } = JSON.parse(raw);
            this.show(String(message), type, Number(duration));
        } catch {
            // Ignore a malformed flash payload.
        }
    }

    /**
     * Convenience methods for different notification types
     * @param {string} message
     * @param {number} duration
     */
    success(message, duration = 3000) {
        this.show(message, 'success', duration);
    }

    /**
     * @param {string} message
     * @param {number} duration
     */
    error(message, duration = 3000) {
        this.show(message, 'error', duration);
    }

    /**
     * @param {string} message
     * @param {number} duration
     */
    warning(message, duration = 3000) {
        this.show(message, 'warning', duration);
    }

    /**
     * @param {string} message
     * @param {number} duration
     */
    info(message, duration = 3000) {
        this.show(message, 'info', duration);
    }
}

// Create global notification instance
window.notification = new AppNotification();

// Backward compatibility: also expose as a function
/**
 * @param {string} message
 * @param {NotificationType} type
 * @param {number} duration
 */
window.showNotification = function(message, type = 'success', duration = 3000) {
    window.notification.show(message, type, duration);
};
