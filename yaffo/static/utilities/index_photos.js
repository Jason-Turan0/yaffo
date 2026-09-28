// @ts-check

/**
 * @typedef {Object} IndexPhotoOptions
 * @property {boolean} canScan
 * @property {boolean} canSync
 * @property {boolean} hasActiveJobs
 * @property {number} failedCount
 * @property {string[]} mediaDirs
 *
 * @typedef {Object} IndexPhotoConfig
 * @property {{ utilities_index_photos_scan: string, utilities_sync_photos: string, utilities_reindex_library: string, utilities_retry_failed: string, utilities_regenerate_thumbnails: string }} urls
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
 * @typedef {{ type: 'done', total_filesystem: number, total_imported: number, total_indexed: number, unindexed: UnindexedPhoto[], orphaned: OrphanedPhoto[], empty_roots: string[], missing_thumbnails: number | null }} DoneRecord
 * @typedef {{ type: 'error', message: string }} ErrorRecord
 * @typedef {ProgressRecord | DoneRecord | ErrorRecord} ScanRecord
 *
 * @typedef {Object} IndexPhotosApi
 * @property {() => Promise<void>} runScan
 * @property {() => Promise<void>} startIndexNew
 * @property {() => Promise<void>} startRemoveOrphaned
 * @property {() => Promise<void>} startReindex
 * @property {() => Promise<void>} startRetryFailed
 * @property {() => Promise<void>} startRegenerateThumbnails
 */

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
const indexPhotosApi = window.PHOTO_ORGANIZER.indexPhotos =
    /** @type {IndexPhotosNamespace} */ (window.PHOTO_ORGANIZER.indexPhotos || {});

// The Index Photos page renders instantly, then streams the (slow) media-dir scan as
// NDJSON: `progress` records drive the live "Total on Filesystem" counter; the final
// `done` record fills the stats and the Library status cards. Each problem gets a
// card with its own fix; "Everything is in sync" shows only when there are none.
// Cap how many rows the file tables render — listing tens of thousands of paths is
// slow and not useful. The fixes still act on the full lists held in memory.
const MAX_DISPLAY_ROWS = 200;

/**
 * @param {IndexPhotoOptions} opts
 * @param {I18nService} i18n
 * @param {IndexPhotoConfig} config
 * @returns {IndexPhotosApi}
 */
const initIndexPhotos = (opts, i18n, config) => {
    const { canScan, canSync, hasActiveJobs, failedCount = 0, mediaDirs } = opts;
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
     * @returns {HTMLButtonElement | null}
     */
    const button = (id) => /** @type {HTMLButtonElement | null} */ (document.getElementById(id));

    /**
     * @param {string} id
     * @param {number | null} value  null: couldn't be checked
     */
    const setStat = (id, value) => {
        const node = document.getElementById(id);
        if (node) node.textContent = value === null ? '—' : i18n.number(Number(value));
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

    /**
     * Show or hide one Library status card, with its count in the title.
     * @param {string} key  the card's issue key (issue-<key>)
     * @param {number} count
     * @param {string} titleKey
     * @returns {HTMLElement | null}  the card's file panel, when it has one
     */
    const showIssue = (key, count, titleKey) => {
        const card = document.getElementById(`issue-${key}`);
        if (card) card.hidden = !count;
        const title = document.getElementById(`issue-${key}-title`);
        if (title && count) title.textContent = i18n.t(titleKey, { count, formattedCount: i18n.number(count) });
        return document.getElementById(`issue-${key}-files`);
    };

    const renderUnindexed = () => {
        const files = showIssue('unindexed', unindexed.length, 'utilities:indexPhotos.issues.unindexed');
        if (!files || !unindexed.length) return;
        files.replaceChildren();
        if (mediaDirs.length > 0) {
            const dirs = el('p', 'section-description');
            dirs.append(document.createTextNode(i18n.t('utilities:indexPhotos.mediaDirectories')));
            mediaDirs.forEach((dir, i) => {
                if (i) dirs.append(document.createTextNode(', '));
                dirs.append(el('code', null, dir));
            });
            files.append(dirs);
        }
        files.append(buildTable([
            i18n.t('utilities:indexPhotos.filename'),
            i18n.t('utilities:indexPhotos.location'),
        ], unindexed.slice(0, MAX_DISPLAY_ROWS).map((p) => [p.filename, p.full_path])));
        if (unindexed.length > MAX_DISPLAY_ROWS) files.append(truncationNote(unindexed.length));
    };

    // Keep these labels in sync with ORPHAN_* in yaffo/utils/file_sync.py.
    /** @type {Record<string, string>} */
    const ORPHAN_REASON_LABELS = {
        missing: 'utilities:indexPhotos.orphanReasons.missing',
        unconfigured: 'utilities:indexPhotos.orphanReasons.unconfigured',
    };

    const renderOrphaned = () => {
        const files = showIssue('orphaned', orphaned.length, 'utilities:indexPhotos.issues.orphaned');
        if (!files || !orphaned.length) return;
        files.replaceChildren(buildTable([
            i18n.t('utilities:indexPhotos.photoId'),
            i18n.t('utilities:indexPhotos.reason'),
            i18n.t('utilities:indexPhotos.location'),
        ], orphaned.slice(0, MAX_DISPLAY_ROWS).map((p) => [
            i18n.number(p.id),
            p.reason && ORPHAN_REASON_LABELS[p.reason]
                ? i18n.t(ORPHAN_REASON_LABELS[p.reason])
                : p.reason || '—',
            p.full_path,
        ])));
        if (orphaned.length > MAX_DISPLAY_ROWS) files.append(truncationNote(orphaned.length));
    };

    /**
     * Warnings above the cards: media folders that hold no media files (usually a
     * drive that didn't mount, whose items then read as orphaned), and a thumbnail
     * folder that couldn't be checked.
     * @param {string[]} emptyFolders
     * @param {boolean} thumbnailsUnchecked
     */
    const showWarnings = (emptyFolders, thumbnailsUnchecked) => {
        const container = document.getElementById('scan-warnings');
        if (!container) return;
        container.replaceChildren();
        if (emptyFolders.length) {
            container.append(el('div', 'alert alert-warning', i18n.t('utilities:indexPhotos.emptyFolders', {
                count: emptyFolders.length,
                folders: i18n.list(emptyFolders),
            })));
        }
        if (thumbnailsUnchecked) {
            container.append(el('div', 'alert alert-warning',
                i18n.t('utilities:indexPhotos.thumbnailFolderUnavailable')));
        }
        container.hidden = container.childElementCount === 0;
    };

    /**
     * @param {DoneRecord} record
     */
    const renderStatus = (record) => {
        const missing = record.missing_thumbnails ?? null;
        const emptyFolders = record.empty_roots || [];
        renderUnindexed();
        renderOrphaned();
        showIssue('missing-thumbnails', missing || 0, 'utilities:indexPhotos.missingThumbnails.title');
        showWarnings(emptyFolders, missing === null);

        const indexNew = button('index-new-button');
        if (indexNew) indexNew.disabled = !canSync || hasActiveJobs;
        const removeOrphaned = button('remove-orphaned-button');
        if (removeOrphaned) removeOrphaned.disabled = !canSync || hasActiveJobs;
        const regenerate = button('regenerate-thumbnails-button');
        if (regenerate) regenerate.disabled = hasActiveJobs;

        const inSync = document.getElementById('status-in-sync');
        if (inSync) {
            inSync.hidden = Boolean(unindexed.length || orphaned.length || missing !== 0
                || failedCount || emptyFolders.length);
        }
    };

    /**
     * Start one of the page's fixes: POST, announce, reload so its job card shows.
     * `keys` names the i18n group holding its button/starting/started/startFailed/error
     * strings; `countField` is the response field the "started" message counts.
     * @param {HTMLButtonElement | null} trigger
     * @param {string} url
     * @param {string} keys
     * @param {{ body?: object, countField?: string }} [options]
     */
    const startFix = async (trigger, url, keys, { body, countField } = {}) => {
        if (!trigger) return;
        trigger.disabled = true;
        trigger.textContent = i18n.t(`${keys}.starting`);
        const restore = () => {
            trigger.disabled = false;
            trigger.textContent = i18n.t(`${keys}.button`);
        };
        try {
            const response = await fetch(url, body === undefined ? { method: 'POST' } : {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = /** @type {Record<string, unknown>} */ (
                await response.json().catch(() => ({}))
            );
            if (response.ok) {
                const count = countField ? data[countField] : undefined;
                window.notification.success(i18n.t(`${keys}.started`, { count }));
                window.location.reload();
            } else {
                const error = typeof data.error === 'string' ? data.error : '';
                window.notification.failure(error || i18n.t(`${keys}.startFailed`));
                restore();
            }
        } catch (error) {
            const reason = error instanceof Error ? error.message : String(error);
            window.notification.failure(i18n.t(`${keys}.error`, { reason }));
            restore();
        }
    };

    // Index the new files only; the orphaned entries are their own decision.
    const startIndexNew = () => startFix(
        button('index-new-button'), config.urls.utilities_sync_photos, 'utilities:indexPhotos.indexNew',
        { body: { files_to_index: unindexed.map((p) => p.full_path), files_to_delete: [] } });

    const startRemoveOrphaned = () => startFix(
        button('remove-orphaned-button'), config.urls.utilities_sync_photos, 'utilities:indexPhotos.removeOrphaned',
        { body: { files_to_index: [], files_to_delete: orphaned.map((p) => p.id) } });

    // Index the files that failed permanently again. File sync leaves them alone until
    // they change, so this is how the user asks. They have no faces yet, so nothing is
    // lost: no confirmation.
    const startRetryFailed = () => startFix(
        button('retry-failed-button'), config.urls.utilities_retry_failed, 'utilities:indexPhotos.retryFailed',
        { countField: 'media_item_count' });

    // Rebuild missing face crops and posters from the photos and videos. Faces keep
    // their people and ignored status (Reindex would drop them), so no confirmation.
    const startRegenerateThumbnails = () => startFix(
        button('regenerate-thumbnails-button'), config.urls.utilities_regenerate_thumbnails,
        'utilities:indexPhotos.missingThumbnails', { countField: 'thumbnail_count' });

    // The status fixes only touch what's wrong. This re-indexes what's already there —
    // the way to rebuild derived data (faces, sizes, metadata) after an indexing
    // change. Every face is re-detected, so every person assignment goes: confirm.
    const startReindex = async () => {
        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: i18n.t('utilities:indexPhotos.reindex.title'),
            message: i18n.t('utilities:indexPhotos.reindex.confirm'),
            confirmText: i18n.t('utilities:indexPhotos.reindex.action'),
            confirmClass: 'btn-danger',
        });
        if (!confirmed) return;
        await startFix(button('reindex-button'), config.urls.utilities_reindex_library,
            'utilities:indexPhotos.reindex', { countField: 'media_item_count' });
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
            setStat('stat-missing-thumbnails', record.missing_thumbnails ?? null);
            setStatus('');
            renderStatus(record);
        } else if (record.type === 'error') {
            setStatus('');
            window.notification.failure(i18n.t('utilities:indexPhotos.scan.error', {
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
        } catch {
            setStatus('');
            window.notification.failure(i18n.t('utilities:indexPhotos.scan.failed'));
        }
    };

    /** @type {[string, () => Promise<void>][]} */
    const actions = [
        ['index-new-button', startIndexNew],
        ['remove-orphaned-button', startRemoveOrphaned],
        ['reindex-button', startReindex],
        ['retry-failed-button', startRetryFailed],
        ['regenerate-thumbnails-button', startRegenerateThumbnails],
    ];
    actions.forEach(([id, action]) => button(id)?.addEventListener('click', action));
    if (canScan) runScan();

    return { runScan, startIndexNew, startRemoveOrphaned, startReindex, startRetryFailed, startRegenerateThumbnails };
};

indexPhotosApi.init = initIndexPhotos;
