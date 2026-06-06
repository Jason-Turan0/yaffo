window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initPageDetail = (pageTitle) => {
    const confirmDelete = async () => {
        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: 'Delete Page',
            message: `Delete "${pageTitle}"? This cannot be undone.`,
            confirmText: 'Delete',
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