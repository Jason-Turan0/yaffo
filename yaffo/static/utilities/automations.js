// @ts-check

/**
 * A single host-API action intercepted during a dry-run test.
 * @typedef {Object} AutomationTestAction
 * @property {string} name - host-API function name, e.g. "tag_media_items".
 * @property {string} summary - human-readable one-line description.
 * @property {unknown[]} [args] - the raw arguments the action was called with.
 *
 * The payload rendered by a Test dry-run.
 * @typedef {Object} AutomationTestResult
 * @property {boolean} success
 * @property {string} code_source - which code version ran (working/published).
 * @property {{ media_item_ids?: number[] }} context
 * @property {AutomationTestAction[]} actions
 * @property {string} [error]
 * @property {unknown} [value] - the automation's return value, if any.
 *
 * The last file/folder the user picked for a scoped Test run.
 * @typedef {Object} AutomationTestSelection
 * @property {string} path
 * @property {string} mode
 */

const automationsWindow = window;

automationsWindow.PHOTO_ORGANIZER = automationsWindow.PHOTO_ORGANIZER || {};
automationsWindow.PHOTO_ORGANIZER.automations = automationsWindow.PHOTO_ORGANIZER.automations || {};

const automations = automationsWindow.PHOTO_ORGANIZER.automations;

// Open the in-app path picker in the chosen mode (folder|file|any); returns the
// selected path or null. Shared by the Test and Run controls, which both scope an
// action to a user-picked file/folder. Opens at `defaultPath` (the first configured
// media dir) when given, so the user starts in their library rather than at home.
/**
 * @param {AppConfig} config
 * @param {FolderPickerMode} mode
 * @param {string | null} [defaultPath]
 * @returns {Promise<string | null>}
 */
const pickAutomationPath = async (config, mode, defaultPath = null) => {
    return automationsWindow.PHOTO_ORGANIZER.pickFolder({ mode, startPath: defaultPath });
};

// Confirm + submit the hidden delete form for the selected automation. (The "New
// automation" modal is wired globally in utilities/_base.js.)
/**
 * @param {string} selectedName
 * @param {I18nService} i18n
 */
automations.initAutomationDelete = (selectedName, i18n) => {
    const deleteButton = document.getElementById('delete-automation-button');
    if (!deleteButton) return;
    deleteButton.addEventListener('click', async () => {
        const confirmed = await automationsWindow.PHOTO_ORGANIZER.confirmDialog({
            title: i18n.t('utilities:automations.delete.title'),
            message: i18n.t('utilities:automations.delete.message', { name: selectedName }),
            confirmText: i18n.t('common:delete'),
            confirmClass: 'btn-danger'
        });
        if (confirmed) {
            const form = /** @type {HTMLFormElement | null} */ (document.getElementById('delete-automation-form'));
            form?.submit();
        }
    });
};

// Wire the "Edit details" button to the name/description modal (custom automations
// only). The modal posts a normal form that redirects back, refreshing the header
// and the sidebar name.
automations.initAutomationDetails = () => {
    const button = document.getElementById('edit-automation-button');
    const components = automationsWindow.PHOTO_ORGANIZER.COMPONENTS;
    if (!button || !components || !components.modal) return;
    const modal = components.modal.init('editAutomationModal');
    button.addEventListener('click', modal.open);
};

// Wire "Run...": pick a file or folder and run over the indexed photos under it.
// The run is enqueued async and shows up in Run history once a worker records it.
/**
 * @param {string} runUrl
 * @param {AppConfig} config
 * @param {string | null} defaultPath
 * @param {I18nService} i18n
 */
automations.initAutomationRunNow = (runUrl, config, defaultPath = null, i18n) => {
    /**
     * @param {HTMLButtonElement} button
     * @param {{ path: string } | null} body
     */
    const post = async (button, body) => {
        button.disabled = true;
        try {
            const response = await fetch(runUrl, {
                method: 'POST',
                headers: body ? { 'Content-Type': 'application/json' } : {},
                body: body ? JSON.stringify(body) : undefined,
            });
            if (response.ok) {
                automationsWindow.notification.success(i18n.t('utilities:automations.run.started'));
            } else {
                const data = await response.json().catch(() => ({}));
                automationsWindow.notification.error(data.error || i18n.t('utilities:automations.run.startFailed'));
            }
        } catch (error) {
            automationsWindow.notification.error(i18n.t('utilities:automations.run.startFailed'));
        } finally {
            button.disabled = false;
        }
    };

    document.querySelectorAll('.js-run-files').forEach((element) => {
        const button = /** @type {HTMLButtonElement} */ (element);
        button.addEventListener('click', async () => {
            const mode = /** @type {FolderPickerMode} */ (button.dataset.mode || 'any');
            const path = await pickAutomationPath(config, mode, defaultPath);
            if (path) post(button, { path });
        });
    });
};

// Wire the "Configure" button to the settings modal (system automations that
// declare config fields, e.g. auto-assign-faces' match threshold). The modal posts
// a normal form that redirects back, refreshing the page with the new value.
automations.initAutomationConfigure = () => {
    const button = document.getElementById('configure-automation-button');
    const components = automationsWindow.PHOTO_ORGANIZER.COMPONENTS;
    if (!button || !components || !components.modal) return;
    const modal = components.modal.init('configureAutomationModal');
    button.addEventListener('click', modal.open);
};

// Run the automation's code in a sandbox dry-run and render what it did: the host-API
// actions intercepted during the run, the captured output, and any error. Changes
// nothing (no Job recorded; the host surface is read-only).
/**
 * @param {string} slug
 * @param {AppConfig} config
 * @param {string | null} defaultPath
 * @param {I18nService} i18n
 */
automations.initAutomationTest = (slug, config, defaultPath = null, i18n) => {
    // Code-version toggle (working draft vs active/published). The visible view is the
    // version a Test runs against. The toggle is only rendered when there's an
    // unpublished draft; otherwise there's a single (published) view.
    const toggleButtons = document.querySelectorAll('.js-code-toggle');
    const codeViews = document.querySelectorAll('.js-code-view');
    const activeToggle = /** @type {HTMLElement | null} */ (document.querySelector('.js-code-toggle.active'));
    let currentVersion = toggleButtons.length
        ? (activeToggle?.dataset.version || 'working')
        : 'published';
    toggleButtons.forEach((element) => {
        const toggle = /** @type {HTMLElement} */ (element);
        toggle.addEventListener('click', () => {
            currentVersion = toggle.dataset.version || 'working';
            toggleButtons.forEach((b) => b.classList.toggle('active', b === toggle));
            codeViews.forEach((viewElement) => {
                const view = /** @type {HTMLElement} */ (viewElement);
                view.hidden = view.dataset.version !== currentVersion;
            });
        });
    });

    const button = /** @type {HTMLButtonElement | null} */ (document.getElementById('automation-test-button'));
    const resultEl = document.getElementById('automation-test-result');
    if (!button || !resultEl) return;

    // The last file/folder picked, displayed with the dry-run output.
    /** @type {AutomationTestSelection | null} */
    let selection = null;

    /**
     * @param {string} tag
     * @param {string | null} [className]
     * @param {string} [text]
     * @returns {HTMLElement}
     */
    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };

    /** @param {{ media_item_ids?: number[] }} ctx */
    const photoCount = (ctx) => {
        const n = (ctx.media_item_ids || []).length;
        return i18n.t('utilities:automations.test.photoCount', {
            count: n,
            formattedCount: i18n.number(n),
        });
    };

    // Action name -> a generic label for a grouped run, e.g. "tag_media_items" -> "Tag media items".
    /** @param {string} name */
    const humanize = (name) => {
        const key = `utilities:automations.test.actionNames.${name}`;
        const translated = i18n.t(key);
        if (translated !== key) return translated;
        const words = name.replace(/_/g, ' ');
        return words.charAt(0).toUpperCase() + words.slice(1);
    };

    /** @param {AutomationTestResult} data */
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
            /** @type {AutomationTestAction[][]} */
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

    /**
     * @param {HTMLButtonElement} clicked
     * @param {() => Promise<Response>} doFetch
     */
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
            automationsWindow.notification.error(i18n.t('utilities:automations.test.runFailed'));
        } finally {
            clicked.disabled = false;
            clicked.textContent = label;
        }
    };

    /**
     * @param {HTMLButtonElement} clicked
     * @param {string} path
     */
    const runFiles = (clicked, path) => run(clicked, () =>
        fetch(config.buildUrl('automations_test_files', { slug }), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, version: currentVersion }),
        }));

    /**
     * @param {string} path
     * @param {string} mode
     */
    const setSelection = (path, mode) => {
        selection = { path, mode };
    };

    // Pick a file/folder, remember it, and run against it.
    button.addEventListener('click', async () => {
        const mode = /** @type {FolderPickerMode} */ (button.dataset.mode || 'any');
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
/**
 * @param {I18nService} [i18n]
 * @param {CronBuilderApi} [cronBuilder]
 */
automations.initTriggerEditor = (
    i18n = automationsWindow.PHOTO_ORGANIZER.i18n,
    cronBuilder = /** @type {CronBuilderApi} */ (automationsWindow.PHOTO_ORGANIZER.COMPONENTS.cronBuilder),
) => {
    /**
     * @param {EventTarget | Element | null} target
     * @returns {HTMLElement | null}
     */
    const areaFor = (target) => {
        const origin = target instanceof Element ? target : null;
        const container = origin?.closest('#automation-triggers');
        return /** @type {HTMLElement | null} */ (container?.querySelector('.automation-trigger-add') ?? null);
    };

    /**
     * @param {HTMLElement | null} area
     * @param {{ cron?: string, triggerId?: string, title: string }} opts
     */
    const openSchedule = (area, { cron, triggerId, title }) => {
        if (!area) return;
        /** @type {HTMLInputElement} */ (area.querySelector('[name="edit_trigger_id"]')).value = triggerId || '';
        /** @type {HTMLElement} */ (area.querySelector('.schedule-editor-title')).textContent = title;
        const mount = /** @type {HTMLElement} */ (area.querySelector('[data-cron-builder]'));
        cronBuilder.initAll(mount);
        if (cron) cronBuilder.setCron(mount, cron); else cronBuilder.reset(mount);
        area.classList.remove('adding-event');
        area.classList.add('adding-schedule');
        area.scrollIntoView({ block: 'nearest' });
    };

    /** @param {HTMLElement | null} area */
    const openEvent = (area) => {
        if (!area) return;
        area.classList.remove('adding-schedule');
        area.classList.add('adding-event');
        area.scrollIntoView({ block: 'nearest' });
    };

    /** @param {HTMLElement | null} area */
    const collapse = (area) => {
        if (!area) return;
        /** @type {HTMLInputElement} */ (area.querySelector('[name="edit_trigger_id"]')).value = '';
        area.classList.remove('adding-schedule', 'adding-event');
    };

    // Gate Save on cron validity. The builder reports preset/builder output as valid
    // outright; only the Advanced field is checked against the server (croniter is the
    // authoritative source), debounced and guarded against stale responses. Save is
    // disabled whenever the cron is invalid; the error line shows only for a non-empty
    // invalid expression (an empty field already reads "Enter a cron…").
    /**
     * @param {HTMLElement} area
     * @param {{ valid: boolean, showError: boolean }} state
     */
    const applyValidity = (area, { valid, showError }) => {
        /** @type {HTMLButtonElement} */ (area.querySelector('.js-save-schedule')).disabled = !valid;
        /** @type {HTMLElement} */ (area.querySelector('.schedule-editor-error')).hidden = !showError;
    };

    /** @type {number | undefined} */
    let validateTimer;
    /**
     * @param {HTMLElement} area
     * @param {string} cron
     */
    const validateOnServer = (area, cron) => {
        clearTimeout(validateTimer);
        if (!cron) { applyValidity(area, { valid: false, showError: false }); return; }
        validateTimer = setTimeout(async () => {
            const url = `${automationsWindow.APP_CONFIG.urls.automations_validate_cron}?cron=${encodeURIComponent(cron)}`;
            try {
                const { valid } = await (await fetch(url)).json();
                if (/** @type {HTMLInputElement} */ (area.querySelector('[name="cron"]')).value === cron) {
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
        const origin = event.target instanceof Element ? event.target : null;
        if (!origin) return;
        const add = origin.closest('.js-add-schedule');
        const edit = /** @type {HTMLElement | null} */ (origin.closest('.js-edit-schedule'));
        const addEvent = origin.closest('.js-add-event');
        const cancel = origin.closest('.js-cancel');
        if (add) {
            openSchedule(areaFor(add), { title: i18n.t('utilities:automations.triggers.addSchedule') });
        } else if (edit) {
            openSchedule(areaFor(edit), {
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
/**
 * @param {string} slug
 * @param {string} startStatus
 * @param {AppConfig} config
 * @param {I18nService} i18n
 * @returns {ChatDialogApi | null}
 */
automations.initAutomationChat = (slug, startStatus, config, i18n) => {
    if (!automationsWindow.PHOTO_ORGANIZER.COMPONENTS.initChatDialog) return null;
    return automationsWindow.PHOTO_ORGANIZER.COMPONENTS.initChatDialog('automation-chat', {
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
