window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initThemesPage = (selectedLabel, config) => {
    const newThemeModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('newThemeModal');

    const newThemeButton = document.getElementById('new-theme-button');
    if (newThemeButton) {
        newThemeButton.addEventListener('click', newThemeModal.open);
    }

    const renameButton = document.getElementById('rename-theme-button');
    if (renameButton) {
        const renameModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('renameThemeModal');
        renameButton.addEventListener('click', renameModal.open);
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

// Wire the theme-design conversation to the shared chat dialog controller. Only the
// theme-specific glue lives here -- the URLs, and the policy that a finished (or
// cancelled) generation reloads so the regenerated CSS and server-rendered transcript
// take effect, while a FAILED run stays open for a follow-up. Built-in themes render a
// read-only transcript (no form), so initChatDialog returns null and this is inert.
window.PHOTO_ORGANIZER.initThemeChat = (slug, startStatus, config) => {
    return window.PHOTO_ORGANIZER.initChatDialog('theme-chat', {
        startStatus,
        statusUrl: () => config.buildUrl('themes_status', { slug }),
        onSend: async (message) => {
            const response = await fetch(config.buildUrl('themes_chat', { slug }), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message })
            });
            if (response.ok) return { ok: true };
            const body = await response.json().catch(() => ({}));
            return { ok: false, error: body.error };
        },
        onCancel: () => fetch(config.buildUrl('themes_cancel', { slug }), { method: 'POST' }),
        onSettled: (body) => {
            if (body.status !== 'FAILED') window.location.reload();
        },
        cancelConfirm: {
            title: 'Cancel generation',
            message: 'Discard this generation and keep the current theme?',
            confirmText: 'Cancel generation',
        },
    });
};