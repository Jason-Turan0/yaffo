window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

// Confirm + submit the hidden delete form for the selected automation. (The "New
// automation" modal is wired globally in utilities/_base.js.)
window.PHOTO_ORGANIZER.initAutomationDelete = (selectedName) => {
    const deleteButton = document.getElementById('delete-automation-button');
    if (!deleteButton) return;
    deleteButton.addEventListener('click', async () => {
        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: 'Delete Automation',
            message: `Delete "${selectedName}"?\nThis cannot be undone.`,
            confirmText: 'Delete',
            confirmClass: 'btn-danger'
        });
        if (confirmed) {
            document.getElementById('delete-automation-form').submit();
        }
    });
};

// Reveal/populate the collapsed schedule editor. The cron builder stays hidden until
// "Add a schedule" (add mode) or a row's "Edit" (edit mode, pre-populated from the
// trigger's cron) is clicked; Cancel collapses it again. Delegated off document so it
// survives the #automation-triggers HTMX re-renders without re-binding. Save is plain
// HTMX (the edit_trigger_id hidden input tells the server add vs update).
window.PHOTO_ORGANIZER.initScheduleEditor = () => {
    const cronBuilder = window.PHOTO_ORGANIZER.COMPONENTS.cronBuilder;

    const open = (editor, { cron, triggerId, title } = {}) => {
        const mount = editor.querySelector('[data-cron-builder]');
        editor.querySelector('[name="edit_trigger_id"]').value = triggerId || '';
        editor.querySelector('.schedule-editor-title').textContent = title;
        if (cron) cronBuilder.setCron(mount, cron); else cronBuilder.reset(mount);
        editor.classList.add('is-editing');
        editor.scrollIntoView({ block: 'nearest' });
    };

    const close = (editor) => {
        editor.querySelector('[name="edit_trigger_id"]').value = '';
        editor.classList.remove('is-editing');
    };

    const editorFor = (el) => el.closest('#automation-triggers').querySelector('.schedule-editor');

    // Gate Save on cron validity. The builder reports preset/builder output as valid
    // outright; only the Advanced field is checked against the server (croniter is the
    // authoritative source), debounced and guarded against stale responses.
    // Save is disabled whenever the cron is invalid; the error line shows only for a
    // non-empty invalid expression (an empty field already reads "Enter a cron…").
    const applyValidity = (editor, { valid, showError }) => {
        editor.querySelector('.js-save-schedule').disabled = !valid;
        editor.querySelector('.schedule-editor-error').hidden = !showError;
    };

    let validateTimer = null;
    const validateOnServer = (editor, cron) => {
        clearTimeout(validateTimer);
        if (!cron) { applyValidity(editor, { valid: false, showError: false }); return; }
        validateTimer = setTimeout(async () => {
            const url = `${window.APP_CONFIG.urls.automations_validate_cron}?cron=${encodeURIComponent(cron)}`;
            try {
                const { valid } = await (await fetch(url)).json();
                if (editor.querySelector('[name="cron"]').value === cron) {
                    applyValidity(editor, { valid, showError: !valid });
                }
            } catch { /* leave Save as-is on a network error; the server still re-validates on save */ }
        }, 250);
    };

    document.addEventListener('cron:change', (event) => {
        const editor = editorFor(event.target);
        if (!editor) return;
        if (event.detail.valid === null) validateOnServer(editor, event.detail.cron);
        else { clearTimeout(validateTimer); applyValidity(editor, { valid: true, showError: false }); }
    });

    document.addEventListener('click', (event) => {
        const add = event.target.closest('.js-add-schedule');
        const edit = event.target.closest('.js-edit-schedule');
        const cancel = event.target.closest('.js-cancel-schedule');
        if (add) {
            open(editorFor(add), { title: 'Add a schedule' });
        } else if (edit) {
            open(editorFor(edit), {
                cron: edit.dataset.cronValue,
                triggerId: edit.dataset.triggerId,
                title: 'Edit schedule',
            });
        } else if (cancel) {
            close(editorFor(cancel));
        }
    });
};

// Wire the automation-builder conversation to the shared chat dialog controller. Only
// the automation-specific glue lives here -- the URLs, and the policy that a finished
// (or cancelled) generation reloads so the published code / draft and server-rendered
// transcript take effect, while a FAILED run stays open for a follow-up. System
// automations render a read-only transcript (no form), so initChatDialog returns null.
window.PHOTO_ORGANIZER.initAutomationChat = (slug, startStatus, config) => {
    return window.PHOTO_ORGANIZER.initChatDialog('automation-chat', {
        startStatus,
        statusUrl: () => config.buildUrl('automations_status', { slug }),
        onSend: async (message) => {
            const response = await fetch(config.buildUrl('automations_chat', { slug }), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message })
            });
            if (response.ok) return { ok: true };
            const body = await response.json().catch(() => ({}));
            return { ok: false, error: body.error };
        },
        onCancel: () => fetch(config.buildUrl('automations_cancel', { slug }), { method: 'POST' }),
        onSettled: (body) => {
            if (body.status !== 'FAILED') window.location.reload();
        },
        cancelConfirm: {
            title: 'Cancel generation',
            message: 'Discard this generation and keep the current automation?',
            confirmText: 'Cancel generation',
        },
    });
};