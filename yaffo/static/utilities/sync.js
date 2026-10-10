// @ts-check

/*
 * The sync automation page (templates/utilities/sync_detail.html): folder pickers for
 * the Folder options, Run now, and Test, which shows what a run would do without
 * doing it (routes/utilities/sync_views.py sync_test).
 */

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

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

/** @type {SyncNamespace} */
const syncNamespace = {
    renderTestResult: (container, result, i18n) => {
        container.replaceChildren();
        container.hidden = false;
        if (result.problem_text) {
            container.append(syncEl('p', result.problem_text, 'form-error'));
            return;
        }
        const summary = syncEl('div', undefined, 'sync-test-summary');
        /** @param {string} key @param {number} count */
        const line = (key, count) => {
            if (count) summary.append(syncEl('span', i18n.t(`utilities:sync.test.${key}`, { count }), 'sync-test-stat'));
        };
        if (result.role === 'source') {
            line('newFiles', result.would_add);
            if (summary.childElementCount) container.append(summary);
            if (result.records.length) {
                const table = syncEl('table', undefined, 'data-table');
                const head = syncEl('tr');
                for (const column of ['file', 'size', 'state']) {
                    head.append(syncEl('th', i18n.t(`utilities:sync.test.columns.${column}`)));
                }
                table.append(head);
                for (const record of result.records) {
                    const row = syncEl('tr');
                    row.append(syncEl('td', record.filename), syncEl('td', syncFormatSize(record.size)),
                        syncEl('td', i18n.t(`utilities:sync.test.state.${record.state}`)));
                    table.append(row);
                }
                const wrapper = syncEl('div', undefined, 'table-container');
                wrapper.append(table);
                container.append(wrapper);
            }
            if (result.more) container.append(syncEl('p', i18n.t('utilities:sync.test.more'), 'hint-text'));
        } else {
            line('wouldAdd', result.would_add);
            line('wouldRemove', result.would_remove);
            line('unchanged', result.unchanged);
            line('convert', result.convert);
            line('unsupported', result.unsupported);
            if (!result.would_add && !result.would_remove) {
                summary.append(syncEl('span', i18n.t('utilities:sync.test.nothingToDo'), 'sync-test-stat'));
            }
            container.append(summary);
            const samples = [
                ...result.add_samples.map((name) => [name, 'copy']),
                ...result.remove_samples.map((name) => [name, 'remove']),
            ];
            if (samples.length) {
                const table = syncEl('table', undefined, 'data-table sync-test-table');
                const head = syncEl('thead');
                const headerRow = syncEl('tr');
                headerRow.append(
                    syncEl('th', i18n.t('utilities:sync.test.columns.file')),
                    syncEl('th', i18n.t('utilities:sync.test.columns.action')),
                );
                head.append(headerRow);
                table.append(head);
                const body = syncEl('tbody');
                for (const [name, action] of samples) {
                    const row = syncEl('tr');
                    row.append(syncEl('td', name), syncEl('td', i18n.t(`utilities:sync.test.preview.${action}`)));
                    body.append(row);
                }
                table.append(body);
                const wrapper = syncEl('div', undefined, 'table-container');
                wrapper.append(table);
                container.append(wrapper);
                const total = result.would_add + result.would_remove;
                if (total > samples.length) {
                    container.append(syncEl('p', i18n.t('utilities:sync.test.preview.showing', {
                        shown: samples.length, total,
                    }), 'hint-text sync-test-sample-note'));
                }
            }
        }
        container.append(syncEl('p', i18n.t('utilities:sync.test.nothingChanged'), 'hint-text'));
    },

    initSyncPage: (i18n, config) => {
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
            testButton.addEventListener('click', async () => {
                testButton.disabled = true;
                if (testCard) {
                    testCard.hidden = false;
                    testCard.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                }
                testResult.replaceChildren(syncEl('p', i18n.t('utilities:sync.test.running'), 'hint-text'));
                try {
                    const response = await fetch(testUrl, { method: 'POST' });
                    if (!response.ok) throw new Error(String(response.status));
                    syncNamespace.renderTestResult(testResult, /** @type {SyncTestResult} */ (await response.json()), i18n);
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
