// @ts-check

/**
 * @typedef {Object} IndexPhotoOptions
 * @property {boolean} canScan
 * @property {boolean} canSync
 * @property {boolean} hasActiveJobs
 * @property {string[]} mediaDirs
 *
 * @typedef {Object} I18nService
 * @property {(key: string, options?: Record<string, unknown>) => string} t
 * @property {(value: number) => string} number
 *
 * @typedef {Object} IndexPhotoConfig
 * @property {{ utilities_index_photos_scan: string, utilities_sync_photos: string }} urls
 *
 * @typedef {Object} UnindexedPhoto
 * @property {string} filename
 * @property {string} full_path
 *
 * @typedef {Object} OrphanedPhoto
 * @property {number} id
 * @property {string} [reason]
 * @property {string} full_path
 *
 * @typedef {{ type: 'progress', scanned: number }} ProgressRecord
 * @typedef {{ type: 'done', total_filesystem: number, total_imported: number, total_indexed: number, unindexed: UnindexedPhoto[], orphaned: OrphanedPhoto[] }} DoneRecord
 * @typedef {{ type: 'error', message: string }} ErrorRecord
 * @typedef {ProgressRecord | DoneRecord | ErrorRecord} ScanRecord
 *
 * @typedef {Object} IndexPhotosApi
 * @property {() => Promise<void>} runScan
 * @property {() => Promise<void>} startSync
 */

const appWindow = /** @type {Window & {
    PHOTO_ORGANIZER: {
        initIndexPhotos?: (
            opts: IndexPhotoOptions,
            i18n: I18nService,
            config: IndexPhotoConfig
        ) => IndexPhotosApi,
    },
    notification: {
        success: (message: string) => void,
        error: (message: string) => void,
    },
}} */ (/** @type {unknown} */ (window));

appWindow.PHOTO_ORGANIZER = appWindow.PHOTO_ORGANIZER || {};

// The Index Photos page renders instantly, then streams the (slow) media-dir scan as
// NDJSON: `progress` records drive the live "Total on Filesystem" counter; the final
// `done` record fills the remaining stats + the unindexed/orphaned tables and reveals
// the Sync button when there's work. Sync posts the lists held from that record.
// Cap how many rows the unindexed/orphaned tables render — listing tens of thousands
// of paths is slow and not useful. Sync still acts on the full lists held in memory.
const MAX_DISPLAY_ROWS = 200;

/**
 * @param {IndexPhotoOptions} opts
 * @param {I18nService} i18n
 * @param {IndexPhotoConfig} config
 * @returns {IndexPhotosApi}
 */
const initIndexPhotos = (opts, i18n, config) => {
    const { canScan, canSync, hasActiveJobs, mediaDirs } = opts;
    /** @type {UnindexedPhoto[]} */
    let unindexed = [];
    /** @type {OrphanedPhoto[]} */
    let orphaned = [];

    /**
     * @param {keyof HTMLElementTagNameMap} tag
     * @param {string | null} [className]
     * @param {string | number} [text]
     * @returns {HTMLElement}
     */
    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = String(text);
        return node;
    };

    /**
     * @param {string} id
     * @param {number} value
     */
    const setStat = (id, value) => {
        const node = document.getElementById(id);
        if (node) node.textContent = i18n.number(Number(value));
    };

    const statusEl = document.getElementById('scan-status');
    /**
     * @param {string} text
     */
    const setStatus = (text) => {
        if (!statusEl) return;
        statusEl.textContent = text || '';
        statusEl.hidden = !text;
    };

    /**
     * @param {string[]} headers
     * @param {string[][]} rows
     * @returns {HTMLElement}
     */
    const buildTable = (headers, rows) => {
        const wrapper = el('div', 'table-container');
        const table = el('table', 'data-table');
        const thead = el('thead');
        const headRow = el('tr');
        headers.forEach((h) => headRow.append(el('th', null, h)));
        thead.append(headRow);
        table.append(thead);

        const tbody = el('tbody');
        const frag = document.createDocumentFragment();
        rows.forEach((cells) => {
            const tr = el('tr');
            cells.forEach((cell, i) => {
                const td = el('td');
                // The trailing column is a filesystem path; render it as <code>.
                if (i === cells.length - 1) td.append(el('code', null, cell));
                else td.textContent = cell;
                tr.append(td);
            });
            frag.append(tr);
        });
        tbody.append(frag);
        table.append(tbody);
        wrapper.append(table);
        return wrapper;
    };

    /**
     * @param {number} total
     * @returns {HTMLElement}
     */
    const truncationNote = (total) => el(
        'p',
        'section-description scan-truncation',
        i18n.t('utilities:indexPhotos.truncation', {
            shown: i18n.number(MAX_DISPLAY_ROWS),
            total: i18n.number(total),
        })
    );

    /** @returns {HTMLElement} */
    const buildUnindexedSection = () => {
        const section = el('div', 'section');
        section.append(el('h2', null, i18n.t('utilities:indexPhotos.unindexed.title')));
        const desc = el('p', 'section-description');
        desc.append(document.createTextNode(
            i18n.t('utilities:indexPhotos.unindexed.description')));
        if (mediaDirs.length > 0) {
            desc.append(el('br'));
            desc.append(document.createTextNode(i18n.t('utilities:indexPhotos.mediaDirectories')));
            mediaDirs.forEach((dir, i) => {
                desc.append(el('code', null, dir));
                if (i < mediaDirs.length - 1) desc.append(document.createTextNode(', '));
            });
        }
        section.append(desc);
        const rows = unindexed.slice(0, MAX_DISPLAY_ROWS).map((p) => [p.filename, p.full_path]);
        section.append(buildTable([
            i18n.t('utilities:indexPhotos.filename'),
            i18n.t('utilities:indexPhotos.location'),
        ], rows));
        if (unindexed.length > MAX_DISPLAY_ROWS) section.append(truncationNote(unindexed.length));
        return section;
    };

    // Keep these labels in sync with ORPHAN_* in yaffo/utils/file_sync.py.
    /** @type {Record<string, string>} */
    const ORPHAN_REASON_LABELS = {
        missing: 'utilities:indexPhotos.orphanReasons.missing',
        unconfigured: 'utilities:indexPhotos.orphanReasons.unconfigured',
    };

    /** @returns {HTMLElement} */
    const buildOrphanedSection = () => {
        const section = el('div', 'section');
        section.append(el('h2', null, i18n.t('utilities:indexPhotos.orphaned.title')));
        section.append(el('p', 'section-description',
            i18n.t('utilities:indexPhotos.orphaned.description')));
        const rows = orphaned.slice(0, MAX_DISPLAY_ROWS).map(
            (p) => [
                i18n.number(p.id),
                p.reason && ORPHAN_REASON_LABELS[p.reason]
                    ? i18n.t(ORPHAN_REASON_LABELS[p.reason])
                    : p.reason || '—',
                p.full_path,
            ]);
        section.append(buildTable([
            i18n.t('utilities:indexPhotos.photoId'),
            i18n.t('utilities:indexPhotos.reason'),
            i18n.t('utilities:indexPhotos.location'),
        ], rows));
        if (orphaned.length > MAX_DISPLAY_ROWS) section.append(truncationNote(orphaned.length));
        return section;
    };

    const renderResults = () => {
        const container = document.getElementById('scan-results');
        if (!container) return;
        container.replaceChildren();

        if (unindexed.length === 0 && orphaned.length === 0) {
            const empty = el('div', 'empty-state');
            empty.append(el('h2', null, i18n.t('utilities:indexPhotos.inSync.title')));
            empty.append(el('p', null,
                i18n.t('utilities:indexPhotos.inSync.description')));
            container.append(empty);
            return;
        }
        if (unindexed.length > 0) container.append(buildUnindexedSection());
        if (orphaned.length > 0) container.append(buildOrphanedSection());
    };

    const syncButton = /** @type {HTMLButtonElement | null} */ (
        document.getElementById('sync-button')
    );

    const revealSyncIfWork = () => {
        if (!syncButton) return;
        if (unindexed.length > 0 || orphaned.length > 0) {
            syncButton.hidden = false;
            syncButton.disabled = !canSync || hasActiveJobs;
        }
    };

    const startSync = async () => {
        if (!syncButton) return;
        syncButton.disabled = true;
        syncButton.textContent = i18n.t('utilities:indexPhotos.sync.starting');
        try {
            const response = await fetch(config.urls.utilities_sync_photos, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    files_to_index: unindexed.map((p) => p.full_path),
                    files_to_delete: orphaned.map((p) => p.id),
                }),
            });
            if (response.ok) {
                appWindow.notification.success(i18n.t('utilities:indexPhotos.sync.started'));
                appWindow.location.reload();
            } else {
                const data = /** @type {{ error?: string }} */ (
                    await response.json().catch(() => ({}))
                );
                appWindow.notification.error(
                    data.error || i18n.t('utilities:indexPhotos.sync.startFailed'));
                syncButton.disabled = false;
                syncButton.textContent = i18n.t('utilities:indexPhotos.sync.button');
            }
        } catch (error) {
            const reason = error instanceof Error ? error.message : String(error);
            appWindow.notification.error(i18n.t('utilities:indexPhotos.sync.error', {
                reason,
            }));
            syncButton.disabled = false;
            syncButton.textContent = i18n.t('utilities:indexPhotos.sync.button');
        }
    };

    /**
     * @param {ScanRecord} record
     */
    const handleRecord = (record) => {
        if (record.type === 'progress') {
            setStat('stat-total-filesystem', record.scanned);
        } else if (record.type === 'done') {
            unindexed = record.unindexed;
            orphaned = record.orphaned;
            setStat('stat-total-filesystem', record.total_filesystem);
            setStat('stat-total-imported', record.total_imported);
            setStat('stat-total-indexed', record.total_indexed);
            setStat('stat-unindexed', record.unindexed.length);
            setStat('stat-orphaned', record.orphaned.length);
            setStatus('');
            renderResults();
            revealSyncIfWork();
        } else if (record.type === 'error') {
            setStatus('');
            appWindow.notification.error(i18n.t('utilities:indexPhotos.scan.error', {
                reason: record.message,
            }));
        }
    };

    const runScan = async () => {
        setStatus(i18n.t('utilities:indexPhotos.scan.scanning'));
        try {
            const response = await fetch(config.urls.utilities_index_photos_scan);
            if (!response.ok || !response.body) {
                throw new Error(i18n.t('utilities:indexPhotos.scan.requestFailed'));
            }
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            for (;;) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                let newline;
                while ((newline = buffer.indexOf('\n')) >= 0) {
                    const line = buffer.slice(0, newline).trim();
                    buffer = buffer.slice(newline + 1);
                    if (line) handleRecord(/** @type {ScanRecord} */ (JSON.parse(line)));
                }
            }
            const tail = buffer.trim();
            if (tail) handleRecord(/** @type {ScanRecord} */ (JSON.parse(tail)));
        } catch (error) {
            setStatus('');
            appWindow.notification.error(i18n.t('utilities:indexPhotos.scan.failed'));
        }
    };

    if (syncButton) syncButton.addEventListener('click', startSync);
    if (canScan) runScan();

    return { runScan, startSync };
};

appWindow.PHOTO_ORGANIZER = appWindow.PHOTO_ORGANIZER || {};
appWindow.PHOTO_ORGANIZER.initIndexPhotos = initIndexPhotos;
