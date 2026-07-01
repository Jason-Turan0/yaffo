// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.pages = window.PHOTO_ORGANIZER.pages || {};

/**
 * @param {string} pageTitle
 * @param {I18nService} i18n
 * @returns {PageDetailApi}
 */
window.PHOTO_ORGANIZER.pages.initDetail = (pageTitle, i18n) => {
    /**
     * @param {string} key
     * @param {Record<string, unknown>} [options]
     */
    const t = (key, options = {}) => i18n.t(key, options);

    const confirmDelete = async () => {
        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: t('pages:delete.title'),
            message: t('pages:delete.message', { name: pageTitle }),
            confirmText: t('common:delete'),
            confirmClass: 'btn-danger'
        });
        if (confirmed) {
            const form = document.getElementById('delete-page-form');
            if (form instanceof HTMLFormElement) form.submit();
        }
    };

    const deleteButton = document.getElementById('delete-page-button');
    if (deleteButton) deleteButton.addEventListener('click', confirmDelete);

    return {
        confirmDelete
    };
};
