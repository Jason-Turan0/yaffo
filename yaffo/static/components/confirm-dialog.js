window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

/**
 * Global confirm dialog component
 * Usage:
 * const result = await window.PHOTO_ORGANIZER.confirmDialog({
 *   title: 'Confirm Action',
 *   message: 'Are you sure?',
 *   confirmText: 'Yes',
 *   cancelText: 'No'
 * });
 * if (result) { // user clicked confirm }
 */
window.PHOTO_ORGANIZER.confirmDialog = (options) => {
    return new Promise((resolve) => {
        const i18n = window.PHOTO_ORGANIZER.i18n;
        const modal = document.getElementById('global-confirm-dialog');
        const title = document.getElementById('confirm-dialog-title');
        const message = document.getElementById('confirm-dialog-message');
        const confirmBtn = document.getElementById('confirm-dialog-confirm');
        const cancelBtn = document.getElementById('confirm-dialog-cancel');

        // Set content
        title.textContent = options.title || i18n.t('common:confirm');
        message.textContent = options.message || i18n.t('common:areYouSure');
        confirmBtn.textContent = options.confirmText || i18n.t('common:confirm');
        cancelBtn.textContent = options.cancelText || i18n.t('common:cancel');

        // Set button class
        confirmBtn.className = `btn ${options.confirmClass || 'btn-primary'}`;

        // Show modal
        modal.classList.add('active');

        const cleanup = () => {
            modal.classList.remove('active');
            confirmBtn.removeEventListener('click', onConfirm);
            cancelBtn.removeEventListener('click', onCancel);
            modal.removeEventListener('click', onBackdropClick);
            document.removeEventListener('keydown', onEscape);
        };

        const onConfirm = () => {
            cleanup();
            resolve(true);
        };

        const onCancel = () => {
            cleanup();
            resolve(false);
        };

        const onBackdropClick = (e) => {
            if (e.target === modal) {
                onCancel();
            }
        };

        const onEscape = (e) => {
            if (e.key === 'Escape') {
                onCancel();
            }
        };

        // Add event listeners
        confirmBtn.addEventListener('click', onConfirm);
        cancelBtn.addEventListener('click', onCancel);
        modal.addEventListener('click', onBackdropClick);
        document.addEventListener('keydown', onEscape);
    });
};
