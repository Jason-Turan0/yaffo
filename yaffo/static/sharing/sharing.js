// @ts-check

(() => {
    const selector = '[data-sharing-confirm]';
    const shareTypeSelector = '[data-share-type-select]';
    const copySelector = '[data-copy-text]';

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

    /**
     * @param {HTMLSelectElement} select
     */
    const updateShareTypePanels = (select) => {
        const form = select.closest('form');
        if (!(form instanceof HTMLFormElement)) {
            return;
        }

        const selectedType = select.value;
        form.querySelectorAll('[data-share-type-panel]').forEach((panel) => {
            if (!(panel instanceof HTMLElement)) {
                return;
            }
            const isActive = panel.dataset.shareTypePanel === selectedType;
            panel.hidden = !isActive;
            panel.querySelectorAll('input, select, button, textarea').forEach((control) => {
                if (
                    control instanceof HTMLInputElement ||
                    control instanceof HTMLSelectElement ||
                    control instanceof HTMLButtonElement ||
                    control instanceof HTMLTextAreaElement
                ) {
                    control.disabled = !isActive;
                }
            });
        });
    };

    /**
     * @param {Document|Element} root
     */
    const initShareTypeControls = (root) => {
        root.querySelectorAll(shareTypeSelector).forEach((select) => {
            if (select instanceof HTMLSelectElement) {
                updateShareTypePanels(select);
            }
        });
    };

    /**
     * Copy-to-clipboard for [data-copy-text] buttons (the device ID chip);
     * delegated so it survives the sidebar/section htmx swaps.
     * @param {Event} event
     */
    const handleCopy = async (event) => {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        const button = target.closest(copySelector);
        if (!(button instanceof HTMLElement) || !button.dataset.copyText) {
            return;
        }
        try {
            await navigator.clipboard.writeText(button.dataset.copyText);
        } catch {
            return;
        }
        const original = button.textContent;
        button.textContent = button.dataset.copiedLabel || original;
        setTimeout(() => { button.textContent = original; }, 2000);
    };

    document.body.addEventListener('htmx:confirm', handleConfirm);
    document.body.addEventListener('click', handleCopy);
    document.body.addEventListener('change', (event) => {
        const target = event.target;
        if (target instanceof HTMLSelectElement && target.matches(shareTypeSelector)) {
            updateShareTypePanels(target);
        }
    });
    document.body.addEventListener('htmx:afterSwap', (event) => {
        const target = event.target;
        if (target instanceof Element) {
            initShareTypeControls(target);
        }
    });
    document.addEventListener('DOMContentLoaded', () => initShareTypeControls(document));
})();
