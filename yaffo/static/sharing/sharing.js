// @ts-check

(() => {
    const selector = '[data-sharing-confirm="revoke"]';

    /**
     * @param {Event} event
     */
    const handleConfirm = (event) => {
        if (!(event instanceof CustomEvent)) {
            return;
        }

        const detail = event.detail || {};
        const source = detail.elt;
        if (!(source instanceof Element)) {
            return;
        }

        const button = source.closest(selector);
        if (!(button instanceof HTMLElement)) {
            return;
        }

        event.preventDefault();

        const confirmDialog = window.PHOTO_ORGANIZER?.confirmDialog;
        if (!confirmDialog) {
            return;
        }

        void confirmDialog({
            title: button.dataset.confirmTitle,
            message: button.dataset.confirmMessage || detail.question,
            confirmText: button.dataset.confirmText,
            confirmClass: 'btn-danger'
        }).then((confirmed) => {
            if (confirmed && typeof detail.issueRequest === 'function') {
                detail.issueRequest(true);
            }
        });
    };

    document.body.addEventListener('htmx:confirm', handleConfirm);
})();
