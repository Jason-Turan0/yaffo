window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initPageDetail = (pageTitle, i18n) => {
    const t = (key, options = {}) => i18n.t(key, options);

    const confirmDelete = async () => {
        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: t('pages:delete.title'),
            message: t('pages:delete.message', { name: pageTitle }),
            confirmText: t('common:delete'),
            confirmClass: 'btn-danger'
        });
        if (confirmed) {
            document.getElementById('delete-page-form').submit();
        }
    };

    return {
        confirmDelete
    };
};
