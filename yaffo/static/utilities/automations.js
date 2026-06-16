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

// Wire the "Edit details" button to the name/description modal (custom automations
// only). The modal posts a normal form that redirects back, refreshing the header
// and the sidebar name.
window.PHOTO_ORGANIZER.initAutomationDetails = () => {
    const button = document.getElementById('edit-automation-button');
    const components = window.PHOTO_ORGANIZER.COMPONENTS;
    if (!button || !components || !components.modal) return;
    const modal = components.modal.init('editAutomationModal');
    button.addEventListener('click', modal.open);
};

// Run the automation's code in a sandbox dry-run and render what it did: the host-API
// actions intercepted during the run, the captured output, and any error. Changes
// nothing (no Job recorded; the host surface is read-only).
window.PHOTO_ORGANIZER.initAutomationTest = (slug, config) => {
    const button = document.getElementById('automation-test-button');
    const resultEl = document.getElementById('automation-test-result');
    if (!button || !resultEl) return;

    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };

    const describeContext = (ctx) => {
        if (ctx.type === 'event') {
            const n = (ctx.photo_ids || []).length;
            return `Event ${ctx.event_type} · ${n} photo${n === 1 ? '' : 's'}`;
        }
        return 'Schedule · no context';
    };

    const render = (data) => {
        resultEl.replaceChildren();
        resultEl.hidden = false;
        resultEl.classList.toggle('is-error', !data.success);

        resultEl.append(el('p', 'automation-test-meta',
            `Ran ${data.code_source} code · ${describeContext(data.context)}`));
        if (data.error) resultEl.append(el('pre', 'automation-test-error', data.error));

        const head = el('div', 'automation-test-actions-head');
        head.append(el('h4', 'automation-test-heading', `Actions (${data.actions.length})`));
        if (data.actions.length) {
            const label = el('label', 'automation-test-toggle');
            const toggle = document.createElement('input');
            toggle.type = 'checkbox';
            toggle.addEventListener('change', () => resultEl.classList.toggle('show-details', toggle.checked));
            label.append(toggle, document.createTextNode(' Show details'));
            head.append(label);
        }
        resultEl.append(head);

        if (data.actions.length) {
            const table = el('table', 'automation-test-table');
            const tbody = el('tbody');
            data.actions.forEach((action) => {
                const row = el('tr');
                row.append(el('td', 'test-action-summary', action.summary));
                const args = (action.args || []).map((a) => JSON.stringify(a)).join(', ');
                const detail = el('td', 'test-action-detail automation-test-advanced');
                detail.append(el('code', null, `${action.name}(${args})`));
                row.append(detail);
                tbody.append(row);
            });
            table.append(tbody);
            resultEl.append(table);
        } else {
            resultEl.append(el('p', 'no-data', 'No actions performed.'));
        }

        if (data.value !== null && data.value !== undefined) {
            resultEl.append(el('h4', 'automation-test-heading automation-test-advanced', 'Result'));
            resultEl.append(el('pre', 'automation-test-output thin-scrollbar automation-test-advanced',
                JSON.stringify(data.value, null, 2)));
        }
    };

    button.addEventListener('click', async () => {
        const label = button.textContent;
        button.disabled = true;
        button.textContent = 'Running…';
        try {
            const response = await fetch(config.buildUrl('automations_test', { slug }), { method: 'POST' });
            const data = await response.json().catch(() => ({}));
            if (response.ok) {
                render(data);
            } else {
                resultEl.replaceChildren(el('p', 'automation-test-error', data.error || 'Test failed.'));
                resultEl.hidden = false;
                resultEl.classList.add('is-error');
            }
        } catch {
            window.notification.error('Failed to run the test.');
        } finally {
            button.disabled = false;
            button.textContent = label;
        }
    });
};

// Drive the "add a trigger" area: two buttons ("Add a schedule" / "Add an event")
// each reveal their own panel (only one at a time, via .adding-schedule /
// .adding-event on the .automation-trigger-add container); a row's "Edit" opens the
// schedule panel pre-populated; Cancel collapses back to the buttons. Delegated off
// document so it survives the #automation-triggers HTMX re-renders. Save/Add-event
// are plain HTMX (edit_trigger_id tells the server add vs update for a schedule).
window.PHOTO_ORGANIZER.initTriggerEditor = () => {
    const cronBuilder = window.PHOTO_ORGANIZER.COMPONENTS.cronBuilder;
    const areaFor = (el) => el.closest('#automation-triggers').querySelector('.automation-trigger-add');

    const openSchedule = (area, { cron, triggerId, title }) => {
        area.querySelector('[name="edit_trigger_id"]').value = triggerId || '';
        area.querySelector('.schedule-editor-title').textContent = title;
        const mount = area.querySelector('[data-cron-builder]');
        if (cron) cronBuilder.setCron(mount, cron); else cronBuilder.reset(mount);
        area.classList.remove('adding-event');
        area.classList.add('adding-schedule');
        area.scrollIntoView({ block: 'nearest' });
    };

    const openEvent = (area) => {
        area.classList.remove('adding-schedule');
        area.classList.add('adding-event');
        area.scrollIntoView({ block: 'nearest' });
    };

    const collapse = (area) => {
        area.querySelector('[name="edit_trigger_id"]').value = '';
        area.classList.remove('adding-schedule', 'adding-event');
    };

    // Gate Save on cron validity. The builder reports preset/builder output as valid
    // outright; only the Advanced field is checked against the server (croniter is the
    // authoritative source), debounced and guarded against stale responses. Save is
    // disabled whenever the cron is invalid; the error line shows only for a non-empty
    // invalid expression (an empty field already reads "Enter a cron…").
    const applyValidity = (area, { valid, showError }) => {
        area.querySelector('.js-save-schedule').disabled = !valid;
        area.querySelector('.schedule-editor-error').hidden = !showError;
    };

    let validateTimer = null;
    const validateOnServer = (area, cron) => {
        clearTimeout(validateTimer);
        if (!cron) { applyValidity(area, { valid: false, showError: false }); return; }
        validateTimer = setTimeout(async () => {
            const url = `${window.APP_CONFIG.urls.automations_validate_cron}?cron=${encodeURIComponent(cron)}`;
            try {
                const { valid } = await (await fetch(url)).json();
                if (area.querySelector('[name="cron"]').value === cron) {
                    applyValidity(area, { valid, showError: !valid });
                }
            } catch { /* leave Save as-is on a network error; the server still re-validates on save */ }
        }, 250);
    };

    document.addEventListener('cron:change', (event) => {
        const area = areaFor(event.target);
        if (!area) return;
        if (event.detail.valid === null) validateOnServer(area, event.detail.cron);
        else { clearTimeout(validateTimer); applyValidity(area, { valid: true, showError: false }); }
    });

    document.addEventListener('click', (event) => {
        const add = event.target.closest('.js-add-schedule');
        const edit = event.target.closest('.js-edit-schedule');
        const addEvent = event.target.closest('.js-add-event');
        const cancel = event.target.closest('.js-cancel');
        if (add) {
            openSchedule(areaFor(add), { title: 'Add a schedule' });
        } else if (edit) {
            openSchedule(areaFor(edit), {
                cron: edit.dataset.cronValue,
                triggerId: edit.dataset.triggerId,
                title: 'Edit schedule',
            });
        } else if (addEvent) {
            openEvent(areaFor(addEvent));
        } else if (cancel) {
            collapse(areaFor(cancel));
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