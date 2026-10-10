// @ts-check

/*
 * The sync automation page (templates/utilities/sync_detail.html): folder pickers for
 * the Folder options, the picker for a folder in a signed-in account (remote_folder,
 * routes/utilities/sync_account.py sync_choices), the device-code sign-in, the review
 * before enabling a web sync, Run now, and Test, which shows what a run would do
 * without doing it (routes/utilities/sync_views.py sync_test).
 */

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

/** @param {HTMLFormElement} form */
const initSyncSelection = (form) => {
    const choices = Array.from(form.querySelectorAll('input[name="kind"]'));
    const album = /** @type {HTMLSelectElement | null} */ (form.querySelector('#sync-album'));
    const submit = /** @type {HTMLButtonElement | null} */ (form.querySelector('[data-selection-submit]'));
    const render = () => {
        const selected = choices.find((choice) => choice instanceof HTMLInputElement && choice.checked);
        const kind = selected instanceof HTMLInputElement ? selected.value : '';
        form.querySelectorAll('[data-selection-choice]').forEach((choice) => {
            const active = choice.getAttribute('data-selection-choice') === kind;
            choice.classList.toggle('is-selected', active);
            const detail = /** @type {HTMLElement | null} */ (choice.querySelector('[data-selection-detail]'));
            if (detail) detail.hidden = !active;
        });
        if (album) album.disabled = kind !== 'album';
        if (submit) {
            submit.textContent = kind === 'query' ? submit.dataset.filterLabel || ''
                : kind === 'script' ? submit.dataset.ruleLabel || '' : submit.dataset.saveLabel || '';
        }
    };
    choices.forEach((choice) => choice.addEventListener('change', render));
    render();
};

/** @param {number | null} bytes */
const syncFormatSize = (bytes) => {
    if (bytes === null || bytes === undefined) return '';
    const units = ['B', 'KB', 'MB', 'GB'];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }
    return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)} ${units[unit]}`;
};

/**
 * @param {string} tag
 * @param {string} [text]
 * @param {string} [className]
 */
const syncEl = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
};

/**
 * One remote option's picker: a breadcrumb of opened folders, the folders inside the
 * current one, and Choose. Choosing stores {id, label} (the label is the breadcrumb
 * path) in the option's hidden input and saves the options form.
 * @param {HTMLElement} root
 * @param {Pick<I18nService, 't'>} i18n
 */
const initRemoteOption = (root, i18n) => {
    const url = root.dataset.choicesUrl || '';
    const input = root.querySelector('input[type="hidden"]');
    const value = root.querySelector('.sync-remote-value');
    const picker = /** @type {HTMLElement | null} */ (root.querySelector('[data-remote-picker]'));
    const crumbs = root.querySelector('[data-remote-crumbs]');
    const list = root.querySelector('[data-remote-list]');
    const more = /** @type {HTMLButtonElement | null} */ (root.querySelector('[data-remote-more]'));
    const chooseHere = /** @type {HTMLButtonElement | null} */ (root.querySelector('[data-remote-choose-here]'));
    const status = root.querySelector('[data-remote-status]');
    if (!(input instanceof HTMLInputElement) || !picker || !crumbs || !list || !more || !chooseHere || !status) return;

    /** @type {SyncChoice[]} */
    let path = [];
    /** @type {string | null} */
    let cursor = null;

    /** @param {SyncChoice[]} trail */
    const labelOf = (trail) => trail.map((step) => step.label).join(' › ');

    /** @param {SyncChoice[]} trail */
    const choose = (trail) => {
        const last = trail[trail.length - 1];
        input.value = JSON.stringify({ id: last.id, label: labelOf(trail) });
        if (value instanceof HTMLInputElement) value.value = labelOf(trail);
        else if (value) value.textContent = labelOf(trail);
        picker.hidden = true;
        const form = input.form;
        if (form) form.requestSubmit();
    };

    /** @param {string} text @param {() => void} onClick */
    const crumbButton = (text, onClick) => {
        const button = syncEl('button', text, 'sync-picker-crumb');
        button.setAttribute('type', 'button');
        button.addEventListener('click', onClick);
        return button;
    };

    const renderCrumbs = () => {
        crumbs.replaceChildren();
        const topLabel = i18n.t('utilities:sync.picker.top');
        crumbs.append(path.length
            ? crumbButton(topLabel, () => { path = []; void load(true); })
            : syncEl('span', topLabel, 'sync-picker-here'));
        path.forEach((step, index) => {
            crumbs.append(syncEl('span', '›', 'sync-picker-sep'));
            const last = index === path.length - 1;
            crumbs.append(last
                ? syncEl('span', step.label, 'sync-picker-here')
                : crumbButton(step.label, () => { path = path.slice(0, index + 1); void load(true); }));
        });
        chooseHere.hidden = path.length === 0;
    };

    /** @param {SyncChoice} item */
    const renderItem = (item) => {
        const row = syncEl('li', undefined, 'sync-picker-item');
        let name;
        if (item.has_children) {
            name = syncEl('button', item.label, 'sync-picker-name sync-picker-open');
            name.setAttribute('type', 'button');
            name.setAttribute('aria-label', `${i18n.t('utilities:sync.picker.open')} ${item.label}`);
            name.addEventListener('click', () => { path = [...path, item]; void load(true); });
        } else {
            name = syncEl('span', item.label, 'sync-picker-name');
        }
        name.dataset.icon = 'folder';
        row.append(name);
        if (item.suggested) row.append(syncEl('span', i18n.t('utilities:sync.picker.suggested'), 'chip chip-accent'));
        const pick = syncEl('button', i18n.t('utilities:sync.picker.choose'), 'btn-secondary btn-sm');
        pick.setAttribute('type', 'button');
        pick.setAttribute('aria-label', `${i18n.t('utilities:sync.picker.choose')} ${item.label}`);
        pick.addEventListener('click', () => choose([...path, item]));
        row.append(pick);
        list.append(row);
    };

    /** @param {boolean} fresh */
    const load = async (fresh) => {
        if (fresh) {
            cursor = null;
            list.replaceChildren();
            renderCrumbs();
        }
        status.textContent = i18n.t('utilities:sync.picker.loading');
        more.hidden = true;
        const params = new URLSearchParams();
        const parent = path[path.length - 1];
        if (parent) params.set('parent', parent.id);
        if (cursor) params.set('cursor', cursor);
        const separator = url.includes('?') ? '&' : '?';
        try {
            const response = await fetch(`${url}${separator}${params}`);
            const data = /** @type {SyncChoicesResponse} */ (await response.json());
            if (!response.ok || data.error) {
                status.textContent = data.error || i18n.t('utilities:sync.picker.failed');
                return;
            }
            (data.items || []).forEach(renderItem);
            cursor = data.next_cursor || null;
            more.hidden = !cursor;
            status.textContent = list.childElementCount ? '' : i18n.t('utilities:sync.picker.empty');
        } catch {
            status.textContent = i18n.t('utilities:sync.picker.failed');
        }
    };

    root.querySelectorAll('[data-remote-open]').forEach((opener) => {
        opener.addEventListener('click', () => {
            if (!picker.hidden) return;
            picker.hidden = false;
            path = [];
            void load(true);
        });
    });
    root.querySelectorAll('[data-remote-close]').forEach((closer) => {
        closer.addEventListener('click', () => { picker.hidden = true; });
    });
    more.addEventListener('click', () => void load(false));
    chooseHere.addEventListener('click', () => { if (path.length) choose(path); });
};

/**
 * "Signing in on another device?": get a code, show it with the provider's link, and
 * poll until the user has entered it, then reload the page.
 * @param {HTMLElement} root
 * @param {Pick<I18nService, 't'>} i18n
 */
const initDeviceCode = (root, i18n) => {
    const start = /** @type {HTMLButtonElement | null} */ (root.querySelector('[data-device-start]'));
    const box = /** @type {HTMLElement | null} */ (root.querySelector('[data-device-box]'));
    const link = /** @type {HTMLAnchorElement | null} */ (root.querySelector('[data-device-link]'));
    const code = root.querySelector('[data-device-code]');
    const status = root.querySelector('[data-device-status]');
    const startUrl = root.dataset.startUrl;
    const pollUrl = root.dataset.pollUrl;
    if (!start || !box || !link || !code || !status || !startUrl || !pollUrl) return;

    /** @param {string} handle @param {number} interval */
    const poll = async (handle, interval) => {
        const body = new URLSearchParams({ handle });
        try {
            const response = await fetch(pollUrl, { method: 'POST', body });
            const data = /** @type {SyncDevicePoll} */ (await response.json());
            if (data.status === 'done') {
                status.textContent = i18n.t('utilities:sync.device.done');
                window.location.reload();
                return;
            }
            if (data.status === 'failed') {
                status.textContent = data.error || i18n.t('utilities:sync.device.failed');
                start.disabled = false;
                return;
            }
        } catch {
            // A dropped poll is retried on the next tick.
        }
        window.setTimeout(() => void poll(handle, interval), interval * 1000);
    };

    start.addEventListener('click', async () => {
        start.disabled = true;
        status.textContent = '';
        try {
            const response = await fetch(startUrl, { method: 'POST' });
            const data = /** @type {SyncDeviceStart} */ (await response.json());
            if (!response.ok || !data.handle) {
                status.textContent = data.error || i18n.t('utilities:sync.device.failed');
                start.disabled = false;
                return;
            }
            link.href = data.verification_uri;
            link.textContent = data.verification_uri;
            code.textContent = data.user_code;
            box.hidden = false;
            status.textContent = i18n.t('utilities:sync.device.waiting');
            void poll(data.handle, Math.max(2, data.interval || 5));
        } catch {
            status.textContent = i18n.t('utilities:sync.device.failed');
            start.disabled = false;
        }
    });
};

/** @type {SyncNamespace} */
const syncNamespace = {
    renderTestResult: (container, result, i18n) => {
        container.replaceChildren();
        container.hidden = false;
        if (result.problem_text) {
            container.append(syncEl('p', result.problem_text, 'form-error'));
            return;
        }

        /** @type {Array<[string, number]>} */
        let stats;
        let nothing;
        if (result.role === 'source') {
            /** @type {Record<string, number>} */
            const byState = {};
            for (const record of result.records) byState[record.state] = (byState[record.state] || 0) + 1;
            stats = ['new', 'changed', 'in_library', 'imported', 'deleted_at_source']
                .map((state) => /** @type {[string, number]} */ ([`state.${state}`, byState[state] || 0]));
            nothing = !(byState.new || byState.changed) && i18n.t('utilities:sync.test.nothingNew');
        } else {
            stats = [
                ['stat.copy', result.would_add], ['stat.remove', result.would_remove],
                ['stat.unchanged', result.unchanged], ['stat.convert', result.convert],
                ['stat.unsupported', result.unsupported],
            ];
            nothing = !result.would_add && !result.would_remove && i18n.t('utilities:sync.test.nothingToDo');
        }

        const tiles = syncEl('dl', undefined, 'sync-test-stats');
        for (const [key, count] of stats) {
            if (!count) continue;
            const tile = syncEl('div', undefined, 'sync-test-stat');
            tile.append(syncEl('dt', i18n.t(`utilities:sync.test.${key}`, { count }), 'sync-test-stat-label'),
                syncEl('dd', new Intl.NumberFormat().format(count), 'sync-test-stat-value'));
            tiles.append(tile);
        }
        if (nothing) container.append(syncEl('p', nothing, 'sync-test-nothing'));
        if (tiles.childElementCount) container.append(tiles);

        /** @param {string[]} columns @param {Array<Array<string | HTMLElement>>} rows */
        const table = (columns, rows) => {
            const element = syncEl('table', undefined, 'data-table sync-test-table');
            const head = syncEl('thead');
            const headerRow = syncEl('tr');
            for (const column of columns) headerRow.append(syncEl('th', i18n.t(`utilities:sync.test.columns.${column}`)));
            head.append(headerRow);
            const body = syncEl('tbody');
            for (const cells of rows) {
                const row = syncEl('tr');
                for (const cell of cells) {
                    const td = syncEl('td');
                    td.append(cell);
                    row.append(td);
                }
                body.append(row);
            }
            element.append(head, body);
            const wrapper = syncEl('div', undefined, 'table-container');
            wrapper.append(element);
            container.append(wrapper);
        };
        /** @param {string} text @param {string} tone */
        const chip = (text, tone) => syncEl('span', text, `chip ${tone}`.trim());

        if (result.role === 'source') {
            /** @type {Record<string, string>} */
            const tones = { new: 'chip-accent', changed: 'chip-warning' };
            if (result.records.length) {
                table(['file', 'size', 'state'], result.records.map((record) => [
                    record.filename, syncFormatSize(record.size),
                    chip(i18n.t(`utilities:sync.test.state.${record.state}`), tones[record.state] || ''),
                ]));
            }
            if (result.more) container.append(syncEl('p', i18n.t('utilities:sync.test.more'), 'hint-text sync-test-note'));
        } else {
            const samples = [
                ...result.add_samples.map((name) => [name, chip(i18n.t('utilities:sync.test.preview.copy'), 'chip-accent')]),
                ...result.remove_samples.map((name) => [name, chip(i18n.t('utilities:sync.test.preview.remove'), 'chip-warning')]),
            ];
            if (samples.length) {
                table(['file', 'action'], samples);
                const total = result.would_add + result.would_remove;
                if (total > samples.length) {
                    container.append(syncEl('p', i18n.t('utilities:sync.test.preview.showing', {
                        shown: samples.length, total,
                    }), 'hint-text sync-test-note sync-test-sample-note'));
                }
            }
        }
    },

    initRemoteOption,
    initDeviceCode,

    initSyncPage: (i18n, config) => {
        const selection = document.querySelector('[data-sync-selection]');
        if (selection instanceof HTMLFormElement) initSyncSelection(selection);
        document.querySelectorAll('[data-remote-option]').forEach((element) => {
            initRemoteOption(/** @type {HTMLElement} */ (element), i18n);
        });
        const deviceCode = document.getElementById('sync-device-code');
        if (deviceCode) initDeviceCode(deviceCode, i18n);
        const review = document.getElementById('sync-enable-review');
        const components = window.PHOTO_ORGANIZER.COMPONENTS;
        if (review && components && components.modal && document.getElementById('syncEnableModal')) {
            const modal = components.modal.init('syncEnableModal');
            review.addEventListener('click', modal.open);
        }

        document.querySelectorAll('[data-folder-picker]').forEach((element) => {
            const button = /** @type {HTMLButtonElement} */ (element);
            const input = document.getElementById(button.dataset.folderPicker || '');
            if (!(input instanceof HTMLInputElement)) return;
            button.addEventListener('click', async () => {
                const path = await window.PHOTO_ORGANIZER.pickFolder({
                    mode: 'folder', startPath: input.value || null,
                });
                if (path) input.value = path;
            });
        });

        const runButton = document.getElementById('sync-run-now');
        if (runButton instanceof HTMLButtonElement && runButton.dataset.runUrl) {
            const runUrl = runButton.dataset.runUrl;
            runButton.addEventListener('click', async () => {
                runButton.disabled = true;
                try {
                    const response = await fetch(runUrl, { method: 'POST' });
                    if (response.ok) {
                        window.notification.success(i18n.t('utilities:automations.run.started'));
                    } else {
                        const data = await response.json().catch(() => ({}));
                        window.notification.failure(data.error || i18n.t('utilities:automations.run.startFailed'));
                    }
                } catch {
                    window.notification.failure(i18n.t('utilities:automations.run.startFailed'));
                } finally {
                    runButton.disabled = false;
                }
            });
        }

        const testButton = document.getElementById('sync-test');
        const testResult = document.getElementById('sync-test-result');
        if (testButton instanceof HTMLButtonElement && testResult && testButton.dataset.testUrl) {
            const testUrl = testButton.dataset.testUrl;
            const testCard = document.getElementById('sync-test-card');
            const testMeta = document.getElementById('sync-test-meta');
            document.getElementById('sync-test-close')?.addEventListener('click', () => {
                if (testCard) testCard.hidden = true;
                testButton.focus();
            });
            testButton.addEventListener('click', async () => {
                testButton.disabled = true;
                if (testCard) {
                    testCard.hidden = false;
                    testCard.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                }
                testResult.replaceChildren(syncEl('p', i18n.t('utilities:sync.test.running'), 'hint-text'));
                if (testMeta) testMeta.textContent = '';
                try {
                    const response = await fetch(testUrl, { method: 'POST' });
                    if (!response.ok) throw new Error(String(response.status));
                    syncNamespace.renderTestResult(testResult, /** @type {SyncTestResult} */ (await response.json()), i18n);
                    if (testMeta) {
                        const time = new Intl.DateTimeFormat(undefined, { timeStyle: 'short' }).format(new Date());
                        testMeta.textContent = i18n.t('utilities:sync.test.checked', { time });
                    }
                } catch {
                    testResult.replaceChildren(syncEl('p', i18n.t('utilities:sync.test.failed'), 'form-error'));
                } finally {
                    testButton.disabled = false;
                }
            });
        }
        void config;
    },
};

window.PHOTO_ORGANIZER.sync = syncNamespace;
