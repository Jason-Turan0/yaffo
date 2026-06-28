window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

// Open the in-app path picker in the chosen mode (folder|file|any); returns the
// selected path or null. Shared by the Test and Run controls, which both scope an
// action to a user-picked file/folder. Opens at `defaultPath` (the first configured
// media dir) when given, so the user starts in their library rather than at home.
const pickAutomationPath = async (config, mode, defaultPath = null) => {
    return window.PHOTO_ORGANIZER.pickFolder({ mode, startPath: defaultPath });
};

// Confirm + submit the hidden delete form for the selected automation. (The "New
// automation" modal is wired globally in utilities/_base.js.)
window.PHOTO_ORGANIZER.initAutomationDelete = (selectedName, i18n) => {
    const deleteButton = document.getElementById('delete-automation-button');
    if (!deleteButton) return;
    deleteButton.addEventListener('click', async () => {
        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: i18n.t('utilities:automations.delete.title'),
            message: i18n.t('utilities:automations.delete.message', { name: selectedName }),
            confirmText: i18n.t('common:delete'),
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

// Wire "Run now". An automation whose triggers are all events has a scoped Run
// button that picks a file or folder and runs over the photos under it; anything
// else has a plain button that fires context-less. A trigger-less automation still
// shows the plain button, but clicking it only warns (add a trigger first) and does
// not run. Otherwise the run is enqueued async and shows up in Run history.
window.PHOTO_ORGANIZER.initAutomationRunNow = (runUrl, config, hasTriggers, defaultPath = null, i18n) => {
    const post = async (button, body) => {
        button.disabled = true;
        try {
            const response = await fetch(runUrl, {
                method: 'POST',
                headers: body ? { 'Content-Type': 'application/json' } : {},
                body: body ? JSON.stringify(body) : undefined,
            });
            if (response.ok) {
                notification.success(i18n.t('utilities:automations.run.started'));
            } else {
                const data = await response.json().catch(() => ({}));
                notification.error(data.error || i18n.t('utilities:automations.run.startFailed'));
            }
        } catch (error) {
            notification.error(i18n.t('utilities:automations.run.startFailed'));
        } finally {
            button.disabled = false;
        }
    };

    const plainButton = document.getElementById('run-automation-button');
    if (plainButton) {
        plainButton.addEventListener('click', () => {
            if (!hasTriggers) {
                notification.warning(i18n.t('utilities:automations.run.noTriggers'));
                return;
            }
            post(plainButton, null);
        });
    }

    document.querySelectorAll('.js-run-files').forEach((button) => {
        button.addEventListener('click', async () => {
            const path = await pickAutomationPath(config, button.dataset.mode || 'any', defaultPath);
            if (path) post(button, { path });
        });
    });
};

// Wire the "Configure" button to the settings modal (system automations that
// declare config fields, e.g. auto-assign-faces' match threshold). The modal posts
// a normal form that redirects back, refreshing the page with the new value.
window.PHOTO_ORGANIZER.initAutomationConfigure = () => {
    const button = document.getElementById('configure-automation-button');
    const components = window.PHOTO_ORGANIZER.COMPONENTS;
    if (!button || !components || !components.modal) return;
    const modal = components.modal.init('configureAutomationModal');
    button.addEventListener('click', modal.open);
};

// Run the automation's code in a sandbox dry-run and render what it did: the host-API
// actions intercepted during the run, the captured output, and any error. Changes
// nothing (no Job recorded; the host surface is read-only).
window.PHOTO_ORGANIZER.initAutomationTest = (slug, config, defaultPath = null, i18n) => {
    // Code-version toggle (working draft vs active/published). The visible view is the
    // version a Test runs against. The toggle is only rendered when there's an
    // unpublished draft; otherwise there's a single (published) view.
    const toggleButtons = document.querySelectorAll('.js-code-toggle');
    const codeViews = document.querySelectorAll('.js-code-view');
    let currentVersion = toggleButtons.length
        ? (document.querySelector('.js-code-toggle.active')?.dataset.version || 'working')
        : 'published';
    toggleButtons.forEach((toggle) => {
        toggle.addEventListener('click', () => {
            currentVersion = toggle.dataset.version;
            toggleButtons.forEach((b) => b.classList.toggle('active', b === toggle));
            codeViews.forEach((view) => { view.hidden = view.dataset.version !== currentVersion; });
        });
    });

    const button = document.getElementById('automation-test-button');
    const resultEl = document.getElementById('automation-test-result');
    if (!button || !resultEl) return;

    // The last file/folder picked, displayed with the dry-run output.
    let selection = null;

    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };

    const photoCount = (ctx) => {
        const n = (ctx.media_item_ids || []).length;
        return i18n.t('utilities:automations.test.photoCount', {
            count: n,
            formattedCount: i18n.number(n),
        });
    };

    // Action name -> a generic label for a grouped run, e.g. "tag_media_items" -> "Tag media items".
    const humanize = (name) => {
        const key = `utilities:automations.test.actionNames.${name}`;
        const translated = i18n.t(key);
        if (translated !== key) return translated;
        const words = name.replace(/_/g, ' ');
        return words.charAt(0).toUpperCase() + words.slice(1);
    };

    const render = (data) => {
        resultEl.replaceChildren();
        resultEl.hidden = false;
        resultEl.classList.toggle('is-error', !data.success);

        if (selection) {
            resultEl.append(el('p', 'automation-test-meta', i18n.t('utilities:automations.test.testingOn', {
                mode: selection.mode,
                path: selection.path,
            })));
        }
        resultEl.append(el('p', 'automation-test-meta',
            i18n.t('utilities:automations.test.ranCode', {
                source: data.code_source,
                photoCount: photoCount(data.context),
            })));
        if (data.error) resultEl.append(el('pre', 'automation-test-error', data.error));

        const head = el('div', 'automation-test-actions-head');
        head.append(el('h4', 'automation-test-heading', i18n.t('utilities:automations.test.actions', {
            count: data.actions.length,
            formattedCount: i18n.number(data.actions.length),
        })));
        if (data.actions.length) {
            const label = el('label', 'automation-test-toggle');
            const toggle = document.createElement('input');
            toggle.type = 'checkbox';
            toggle.addEventListener('change', () => resultEl.classList.toggle('show-details', toggle.checked));
            label.append(toggle, document.createTextNode(` ${i18n.t('utilities:automations.test.showDetails')}`));
            head.append(label);
        }
        resultEl.append(head);

        if (data.actions.length) {
            // Collapse a run of the same action into one row with a × count; the
            // per-item detail lives behind "Show details".
            const groups = [];
            data.actions.forEach((action) => {
                const last = groups[groups.length - 1];
                if (last && last[0].name === action.name) last.push(action);
                else groups.push([action]);
            });

            const table = el('table', 'automation-test-table');
            const tbody = el('tbody');
            groups.forEach((group) => {
                const row = el('tr');
                const summary = el('td', 'test-action-summary');
                const detail = el('td', 'test-action-detail automation-test-advanced');
                if (group.length === 1) {
                    summary.textContent = group[0].summary;
                    const args = (group[0].args || []).map((a) => JSON.stringify(a, null , 2)).join(', ');
                    detail.append(el('code', null, `${group[0].name}(${args})`));
                } else {
                    summary.append(document.createTextNode(`${humanize(group[0].name)} `));
                    summary.append(el('span', 'automation-test-count', `× ${i18n.number(group.length)}`));
                    const list = el('ul', 'automation-test-group');
                    group.forEach((action) => list.append(el('li', null, action.summary)));
                    detail.append(list);
                }
                row.append(summary, detail);
                tbody.append(row);
            });
            table.append(tbody);
            resultEl.append(table);
        } else {
            resultEl.append(el('p', 'no-data', i18n.t('utilities:automations.test.noActions')));
        }

        if (data.value !== null && data.value !== undefined) {
            resultEl.append(el('h4', 'automation-test-heading automation-test-advanced',
                i18n.t('utilities:automations.test.result')));
            resultEl.append(el('pre', 'automation-test-output thin-scrollbar automation-test-advanced',
                JSON.stringify(data.value, null, 2)));
        }
    };

    const run = async (clicked, doFetch) => {
        const label = clicked.textContent;
        clicked.disabled = true;
        clicked.textContent = i18n.t('utilities:automations.test.running');
        try {
            const response = await doFetch();
            const data = await response.json().catch(() => ({}));
            if (response.ok) {
                render(data);
            } else {
                resultEl.replaceChildren(el('p', 'automation-test-error',
                    data.error || i18n.t('utilities:automations.test.failed')));
                resultEl.hidden = false;
                resultEl.classList.add('is-error');
            }
        } catch {
            window.notification.error(i18n.t('utilities:automations.test.runFailed'));
        } finally {
            clicked.disabled = false;
            clicked.textContent = label;
        }
    };

    const runFiles = (clicked, path) => run(clicked, () =>
        fetch(config.buildUrl('automations_test_files', { slug }), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, version: currentVersion }),
        }));

    const setSelection = (path, mode) => {
        selection = { path, mode };
    };

    // Pick a file/folder, remember it, and run against it.
    button.addEventListener('click', async () => {
        const mode = button.dataset.mode || 'any';
        const path = await pickAutomationPath(config, mode, defaultPath);
        if (!path) return;
        setSelection(path, 'path');
        runFiles(button, path);
    });
};

// Drive the "add a trigger" area: two buttons ("Add a schedule" / "Add an event")
// each reveal their own panel (only one at a time, via .adding-schedule /
// .adding-event on the .automation-trigger-add container); a row's "Edit" opens the
// schedule panel pre-populated; Cancel collapses back to the buttons. Delegated off
// document so it survives the #automation-triggers HTMX re-renders. Save/Add-event
// are plain HTMX (edit_trigger_id tells the server add vs update for a schedule).
window.PHOTO_ORGANIZER.initTriggerEditor = (i18n = window.PHOTO_ORGANIZER.i18n) => {
    const cronBuilderReady = window.PHOTO_ORGANIZER.COMPONENTS.cronBuilderReady;
    const areaFor = (el) => el.closest('#automation-triggers').querySelector('.automation-trigger-add');

    const openSchedule = async (area, { cron, triggerId, title }) => {
        const cronBuilder = await cronBuilderReady;
        area.querySelector('[name="edit_trigger_id"]').value = triggerId || '';
        area.querySelector('.schedule-editor-title').textContent = title;
        const mount = area.querySelector('[data-cron-builder]');
        cronBuilder.initAll(mount);
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

    document.addEventListener('click', async (event) => {
        const add = event.target.closest('.js-add-schedule');
        const edit = event.target.closest('.js-edit-schedule');
        const addEvent = event.target.closest('.js-add-event');
        const cancel = event.target.closest('.js-cancel');
        if (add) {
            await openSchedule(areaFor(add), { title: i18n.t('utilities:automations.triggers.addSchedule') });
        } else if (edit) {
            await openSchedule(areaFor(edit), {
                cron: edit.dataset.cronValue,
                triggerId: edit.dataset.triggerId,
                title: i18n.t('utilities:automations.triggers.editSchedule'),
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
window.PHOTO_ORGANIZER.initAutomationChat = (slug, startStatus, config, i18n) => {
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
            title: i18n.t('components:chat.cancelGeneration'),
            message: i18n.t('utilities:automations.chat.cancelMessage'),
            confirmText: i18n.t('components:chat.cancelGeneration'),
        },
    });
};
