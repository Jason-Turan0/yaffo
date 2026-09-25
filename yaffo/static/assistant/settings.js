// @ts-check

/**
 * Settings → Assistant: the "Delete all conversations" button. The on/off switch
 * and the model select post with htmx; deleting needs the global confirm dialog,
 * which htmx doesn't use, so it is wired here.
 */

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
const assistantSettings = window.PHOTO_ORGANIZER.assistant =
    /** @type {AssistantNamespace} */ (window.PHOTO_ORGANIZER.assistant || {});

/**
 * @param {I18nService} i18n
 * @param {AppConfig} config
 */
assistantSettings.initSettings = (i18n, config) => {
    const button = document.getElementById('assistant-delete-all');
    const countEl = document.getElementById('assistant-conversation-count');
    if (!(button instanceof HTMLButtonElement)) return;

    button.addEventListener('click', async () => {
        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: i18n.t('assistant:settings.deleteAllTitle'),
            message: i18n.t('assistant:settings.deleteAllMessage'),
            confirmText: i18n.t('assistant:settings.deleteAllConfirm'),
            confirmClass: 'btn-danger',
        });
        if (!confirmed) return;
        try {
            const response = await fetch(config.urls.assistant_delete_all, { method: 'POST' });
            if (!response.ok) throw new Error(String(response.status));
            const body = await response.json();
            const deleted = Number(body.deleted) || 0;
            window.notification.success(i18n.t('assistant:settings.deleted', { count: deleted }));
            if (countEl) {
                countEl.dataset.count = '0';
                countEl.textContent = i18n.t('assistant:settings.conversationCount', { count: 0 });
            }
            button.disabled = true;
        } catch {
            window.notification.error(i18n.t('assistant:settings.deleteFailed'));
        }
    });
};
