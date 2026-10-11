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
    const layoutOption = /** @type {HTMLElement | null} */ (document.querySelector('[data-sync-layout-option]'));
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
        if (layoutOption) layoutOption.hidden = kind === 'script';
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
 * path) in the option's hidden input and saves the options form. A collection that
 * can be created also offers a name field, which stores {label, new: true}.
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

    const newName = root.querySelector('[data-remote-new-name]');
    const useNewName = root.querySelector('[data-remote-new]');
    if (newName instanceof HTMLInputElement && useNewName) {
        const chooseNew = () => {
            const label = newName.value.trim();
            if (!label) return;
            input.value = JSON.stringify({ label, new: true });
            if (value instanceof HTMLInputElement) value.value = label;
            picker.hidden = true;
            const form = input.form;
            if (form) form.requestSubmit();
        };
        useNewName.addEventListener('click', chooseNew);
        newName.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            chooseNew();
        });
    }
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

/**
 * A self-hosted server's address field: the http confirmation shows only while the
 * address starts with http://, and is unticked whenever it's hidden, so it can't
 * approve an address the user no longer sees.
 * @param {ParentNode} root
 */
const initServerAddress = (root) => {
    root.querySelectorAll('[data-server-address]').forEach((group) => {
        const input = /** @type {HTMLInputElement | null} */ (group.querySelector('[data-base-url]'));
        const confirm = /** @type {HTMLElement | null} */ (group.querySelector('[data-allow-http]'));
        const box = /** @type {HTMLInputElement | null} */ (confirm && confirm.querySelector('input[type="checkbox"]'));
        if (!input || !confirm || !box) return;
        const update = () => {
            confirm.hidden = !/^\s*http:\/\//i.test(input.value);
            if (confirm.hidden) box.checked = false;
        };
        input.addEventListener('input', update);
        update();
    });
};

/**
 * One of the service builder's permission requests, as a card: reading pages from
 * new domains (each can be left unticked) or reading the test account live. An
 * answered request is one line saying what was decided.
 * @param {ChatMessage} message
 * @param {Pick<I18nService, 't'>} i18n
 * @param {(id: number, answer: { decision: string, domains?: string[] }) => Promise<void>} respond
 */
const syncPermissionCard = (message, i18n, respond) => {
    /** @type {{ kind?: string, status?: string, domains?: Record<string, string[]>, allowed?: string[], denied?: string[], account?: string }} */
    let request;
    try {
        request = JSON.parse(message.content);
    } catch {
        request = {};
    }
    const card = syncEl('div', undefined, 'chat-message chat-message-permission service-permission');
    const id = Number(message.id);
    const live = request.kind === 'live_reads';
    if (request.status !== 'pending') {
        const decided = request.status === 'superseded' ? i18n.t('utilities:sync.service.superseded')
            : live ? i18n.t(request.status === 'allowed' ? 'utilities:sync.service.liveAllowed' : 'utilities:sync.service.liveDenied',
                { account: request.account || '' })
                : [request.allowed && request.allowed.length
                    ? i18n.t('utilities:sync.service.docsAllowed', { domains: request.allowed.join(', ') }) : '',
                request.denied && request.denied.length
                    ? i18n.t('utilities:sync.service.docsDenied', { domains: request.denied.join(', ') }) : '']
                    .filter(Boolean).join(' ');
        card.classList.add('is-answered');
        card.append(syncEl('p', decided));
        return card;
    }
    /** @param {string} label @param {string} className @param {() => void} onClick */
    const button = (label, className, onClick) => {
        const element = /** @type {HTMLButtonElement} */ (syncEl('button', label, className));
        element.type = 'button';
        element.addEventListener('click', () => {
            card.querySelectorAll('button').forEach((b) => { /** @type {HTMLButtonElement} */ (b).disabled = true; });
            onClick();
        });
        return element;
    };
    const actions = syncEl('div', undefined, 'service-permission-actions');
    if (live) {
        card.append(syncEl('p', i18n.t('utilities:sync.service.liveAsk', { account: request.account || '' })));
        actions.append(button(i18n.t('utilities:sync.service.allow'), 'btn-primary btn-sm', () => void respond(id, { decision: 'allow' })),
            button(i18n.t('utilities:sync.service.dontAllow'), 'btn-secondary btn-sm', () => void respond(id, { decision: 'deny' })));
    } else {
        const domains = Object.entries(request.domains || {});
        card.append(syncEl('p', i18n.t('utilities:sync.service.docsAsk', { count: domains.length })));
        const list = syncEl('ul', undefined, 'service-permission-domains');
        for (const [domain, urls] of domains) {
            const item = syncEl('li');
            const label = syncEl('label', undefined, 'checkbox-label');
            const box = /** @type {HTMLInputElement} */ (syncEl('input'));
            box.type = 'checkbox';
            box.checked = true;
            box.value = domain;
            label.append(box, syncEl('span', i18n.t('utilities:sync.service.docsDomain', { domain, count: urls.length })));
            const pages = syncEl('ul', undefined, 'service-permission-urls');
            for (const url of urls) pages.append(syncEl('li', url));
            item.append(label, pages);
            list.append(item);
        }
        card.append(list, syncEl('p', i18n.t('utilities:sync.service.docsNote'), 'hint-text'));
        actions.append(
            button(i18n.t('utilities:sync.service.allowSelected'), 'btn-primary btn-sm', () => {
                const chosen = [...list.querySelectorAll('input:checked')].map((box) => /** @type {HTMLInputElement} */ (box).value);
                void respond(id, chosen.length ? { decision: 'allow', domains: chosen } : { decision: 'deny' });
            }),
            button(i18n.t('utilities:sync.service.denyAll'), 'btn-secondary btn-sm', () => void respond(id, { decision: 'deny' })));
    }
    card.append(actions);
    return card;
};

/** @type {SyncNamespace} */
const syncNamespace = {
    renderTestResult: (container, result, i18n, config) => {
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
            const byState = result.state_counts;
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
        /** @param {string} name @param {number | null} mediaItemId */
        const photoName = (name, mediaItemId) => {
            if (mediaItemId === null) return name;
            const link = /** @type {HTMLAnchorElement} */ (syncEl('a', name, 'row-link'));
            link.href = config.buildUrl('media_view', { media_item_id: mediaItemId });
            return link;
        };

        if (result.role === 'source') {
            /** @type {Record<string, string>} */
            const tones = { new: 'chip-accent', changed: 'chip-warning' };
            if (result.records.length) {
                table(['file', 'size', 'state'], result.records.map((record) => [
                    photoName(record.filename, record.media_item_id), syncFormatSize(record.size),
                    chip(i18n.t(`utilities:sync.test.state.${record.state}`), tones[record.state] || ''),
                ]));
            }
            if (result.more) {
                const format = new Intl.NumberFormat().format;
                const counted = format(result.total_items);
                const note = result.source_total === null
                    ? i18n.t('utilities:sync.test.partial', { counted })
                    : i18n.t('utilities:sync.test.partialOf', { counted, total: format(result.source_total) });
                container.append(syncEl('p', note, 'hint-text sync-test-note'));
            }
        } else {
            const showDestination = [...result.add_samples, ...result.remove_samples]
                .some((sample) => Boolean(sample.destination));
            /** @param {SyncTestSample} sample @param {string} action @param {string} tone */
            const targetRow = (sample, action, tone) => {
                /** @type {Array<string | HTMLElement>} */
                const cells = [photoName(sample.filename, sample.media_item_id)];
                if (showDestination) cells.push(sample.destination || '');
                cells.push(chip(action, tone));
                return cells;
            };
            const samples = [
                ...result.add_samples.map((sample) => targetRow(sample, i18n.t('utilities:sync.test.preview.copy'), 'chip-accent')),
                ...result.remove_samples.map((sample) => targetRow(sample, i18n.t('utilities:sync.test.preview.remove'), 'chip-warning')),
            ];
            if (samples.length) {
                table(showDestination ? ['file', 'destination', 'action'] : ['file', 'action'], samples);
            }
        }
        if (result.pagination_html) container.insertAdjacentHTML('beforeend', result.pagination_html);
    },

    initRemoteOption,
    initDeviceCode,

    initSyncPage: (i18n, config) => {
        initServerAddress(document);
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
            /** @param {number} [page] @param {number | null} [pageSize] */
            const runTest = async (page = 0, pageSize = null) => {
                testButton.disabled = true;
                if (testCard) {
                    testCard.hidden = false;
                    testCard.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                }
                testResult.replaceChildren(syncEl('p', i18n.t('utilities:sync.test.running'), 'hint-text'));
                if (testMeta) testMeta.textContent = '';
                try {
                    const options = pageSize === null ? { method: 'POST' } : {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ page, page_size: pageSize }),
                    };
                    const response = await fetch(testUrl, options);
                    if (!response.ok) throw new Error(String(response.status));
                    syncNamespace.renderTestResult(testResult, /** @type {SyncTestResult} */ (await response.json()), i18n, config);
                    if (testMeta) {
                        const time = new Intl.DateTimeFormat(undefined, { timeStyle: 'short' }).format(new Date());
                        testMeta.textContent = i18n.t('utilities:sync.test.checked', { time });
                    }
                } catch {
                    testResult.replaceChildren(syncEl('p', i18n.t('utilities:sync.test.failed'), 'form-error'));
                } finally {
                    testButton.disabled = false;
                }
            };
            testButton.addEventListener('click', () => { void runTest(); });
            testResult.addEventListener('click', (event) => {
                if (!(event.target instanceof Element)) return;
                const button = event.target.closest('[data-pagination-page]');
                if (!(button instanceof HTMLButtonElement) || button.disabled || testButton.disabled) return;
                const size = testResult.querySelector('[data-pagination-size]');
                if (!(size instanceof HTMLSelectElement)) return;
                void runTest(Number(button.dataset.paginationPage), Number(size.value));
            });
            testResult.addEventListener('change', (event) => {
                if (!(event.target instanceof HTMLSelectElement) || !event.target.matches('[data-pagination-size]')
                    || testButton.disabled) return;
                void runTest(0, Number(event.target.value));
            });
        }
        void config;
    },

    /**
     * The service builder page: its chat (the shared chat dialog, polling the
     * builder's status), the Publish review, and Delete behind the confirm dialog.
     */
    initServicePage: (slug, startStatus, i18n, config) => {
        initServerAddress(document);
        const components = window.PHOTO_ORGANIZER.COMPONENTS;
        /** @type {ChatDialogApi | null} */
        let chat = null;
        // Reload (to refresh the tabs) only when a builder run this page followed has
        // ended: not on the first poll that just draws the transcript, which finds the
        // builder idle and would otherwise reload forever.
        let following = startStatus === 'IN_PROGRESS';
        /** @param {number} id @param {{ decision: string, domains?: string[] }} answer */
        const respond = async (id, answer) => {
            const response = await fetch(config.buildUrl('sync_service_permission', { slug, message_id: id }), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(answer),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) window.notification.error(body.error || i18n.t('utilities:sync.service.answerFailed'));
            if (chat) {
                following = Boolean(body.started);
                if (body.started) chat.enterRunning();
                else chat.load();
            }
        };
        if (components && components.initChatDialog) {
            chat = components.initChatDialog('service-chat', {
                renderMessages: (messages) => messages.map((message) => (message.type === 'permission'
                    ? syncPermissionCard(message, i18n, respond)
                    : syncEl('div', message.content, `chat-message chat-message-${message.type}`))),
                startStatus,
                statusUrl: () => config.buildUrl('sync_service_status', { slug }),
                onSend: async (message) => {
                    const response = await fetch(config.buildUrl('sync_service_chat', { slug }), {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message }),
                    });
                    if (response.ok) {
                        following = true;
                        return { ok: true };
                    }
                    const body = await response.json().catch(() => ({}));
                    return { ok: false, error: body.error };
                },
                onStatus: (body) => {
                    if (body.status === 'IN_PROGRESS') following = true;
                },
                onCancel: () => fetch(config.buildUrl('sync_service_cancel', { slug }), { method: 'POST' }),
                onSettled: (body) => {
                    const ended = following;
                    following = false;
                    if (ended && body.status !== 'FAILED') window.location.reload();
                },
                cancelConfirm: {
                    title: i18n.t('components:chat.cancelGeneration'),
                    message: i18n.t('utilities:sync.service.cancelMessage'),
                    confirmText: i18n.t('components:chat.cancelGeneration'),
                },
            });
            if (chat) chat.load();  // draw the builder's requests as cards
        }
        const publish = document.getElementById('service-publish');
        if (publish && components && components.modal && document.getElementById('servicePublishModal')) {
            publish.addEventListener('click', components.modal.init('servicePublishModal').open);
        }
        const remove = /** @type {HTMLFormElement | null} */ (document.querySelector('[data-confirm-delete]'));
        if (remove) {
            remove.addEventListener('submit', async (event) => {
                event.preventDefault();
                const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
                    title: i18n.t('utilities:sync.service.deleteTitle'),
                    message: i18n.t('utilities:sync.service.deleteMessage'),
                    confirmText: i18n.t('common:delete'),
                    confirmClass: 'btn-danger',
                });
                if (confirmed) remove.submit();
            });
        }
    },
};

window.PHOTO_ORGANIZER.sync = syncNamespace;
