// @ts-check

/**
 * Settings → Assistant: the "N of M on" count on each folded group of changes,
 * kept in step as its checkboxes change. The switches themselves post with htmx.
 */

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
const assistantSettings = window.PHOTO_ORGANIZER.assistant =
    /** @type {AssistantNamespace} */ (window.PHOTO_ORGANIZER.assistant || {});

/**
 * @param {I18nService} i18n
 */
assistantSettings.initSettings = (i18n) => {
    // Each group's count follows its checkboxes (the server renders the first one).
    document.querySelectorAll('[data-assistant-action-group]').forEach((group) => {
        const count = group.querySelector('[data-assistant-group-count]');
        if (!(count instanceof HTMLElement)) return;
        group.addEventListener('change', () => {
            const on = group.querySelectorAll('input[type="checkbox"]:checked').length;
            count.textContent = i18n.t('assistant:settings.groupCount', { on, total: Number(count.dataset.total) });
        });
    });
};
