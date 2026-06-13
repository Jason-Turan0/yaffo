window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initThemesPage = (selectedLabel, config) => {
    const newThemeModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('newThemeModal');

    const newThemeButton = document.getElementById('new-theme-button');
    if (newThemeButton) {
        newThemeButton.addEventListener('click', newThemeModal.open);
    }

    const confirmDelete = async () => {
        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: 'Delete Theme',
            message: `Delete "${selectedLabel}"?\nThis cannot be undone.`,
            confirmText: 'Delete',
            confirmClass: 'btn-danger'
        });
        if (confirmed) {
            document.getElementById('delete-theme-form').submit();
        }
    };

    const deleteButton = document.getElementById('delete-theme-button');
    if (deleteButton) {
        deleteButton.addEventListener('click', confirmDelete);
    }

    return {
        confirmDelete
    };
};