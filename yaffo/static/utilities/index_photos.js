window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

// The Index Photos page renders instantly, then streams the (slow) media-dir scan as
// NDJSON: `progress` records drive the live "Total on Filesystem" counter; the final
// `done` record fills the remaining stats + the unindexed/orphaned tables and reveals
// the Sync button when there's work. Sync posts the lists held from that record.
// Cap how many rows the unindexed/orphaned tables render — listing tens of thousands
// of paths is slow and not useful. Sync still acts on the full lists held in memory.
const MAX_DISPLAY_ROWS = 200;

window.PHOTO_ORGANIZER.initIndexPhotos = (config, opts) => {
    const { canScan, canSync, hasActiveJobs, mediaDirs } = opts;
    let unindexed = [];
    let orphaned = [];

    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };

    const setStat = (id, value) => {
        const node = document.getElementById(id);
        if (node) node.textContent = Number(value).toLocaleString();
    };

    const statusEl = document.getElementById('scan-status');
    const setStatus = (text) => {
        if (!statusEl) return;
        statusEl.textContent = text || '';
        statusEl.hidden = !text;
    };

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

    const truncationNote = (total) => el('p', 'section-description scan-truncation',
        `Showing the first ${MAX_DISPLAY_ROWS.toLocaleString()} of ${total.toLocaleString()}. Sync processes all of them.`);

    const buildUnindexedSection = () => {
        const section = el('div', 'section');
        section.append(el('h2', null, 'Unindexed Photos'));
        const desc = el('p', 'section-description');
        desc.append(document.createTextNode(
            'The following photos exist in the configured media directories but are not in the database.'));
        if (mediaDirs.length > 0) {
            desc.append(el('br'));
            desc.append(document.createTextNode('Media directories: '));
            mediaDirs.forEach((dir, i) => {
                desc.append(el('code', null, dir));
                if (i < mediaDirs.length - 1) desc.append(document.createTextNode(', '));
            });
        }
        section.append(desc);
        const rows = unindexed.slice(0, MAX_DISPLAY_ROWS).map((p) => [p.filename, p.full_path]);
        section.append(buildTable(['Filename', 'Location'], rows));
        if (unindexed.length > MAX_DISPLAY_ROWS) section.append(truncationNote(unindexed.length));
        return section;
    };

    // Keep these labels in sync with ORPHAN_* in yaffo/utils/file_sync.py.
    const ORPHAN_REASON_LABELS = {
        missing: 'File deleted from disk',
        unconfigured: 'Media directory no longer configured',
    };

    const buildOrphanedSection = () => {
        const section = el('div', 'section');
        section.append(el('h2', null, 'Orphaned Database Entries'));
        section.append(el('p', 'section-description',
            'The following photos are in the database but no longer have a file under the '
            + 'configured media directories — either the file was deleted, or its media '
            + 'directory was removed from Settings. Syncing removes these entries.'));
        const rows = orphaned.slice(0, MAX_DISPLAY_ROWS).map(
            (p) => [String(p.id), ORPHAN_REASON_LABELS[p.reason] || p.reason || '—', p.full_path]);
        section.append(buildTable(['Photo ID', 'Reason', 'Location'], rows));
        if (orphaned.length > MAX_DISPLAY_ROWS) section.append(truncationNote(orphaned.length));
        return section;
    };

    const renderResults = () => {
        const container = document.getElementById('scan-results');
        if (!container) return;
        container.replaceChildren();

        if (unindexed.length === 0 && orphaned.length === 0) {
            const empty = el('div', 'empty-state');
            empty.append(el('h2', null, 'Everything is in sync'));
            empty.append(el('p', null,
                'All photos on the filesystem are indexed, and all database entries have corresponding files.'));
            container.append(empty);
            return;
        }
        if (unindexed.length > 0) container.append(buildUnindexedSection());
        if (orphaned.length > 0) container.append(buildOrphanedSection());
    };

    const syncButton = document.getElementById('sync-button');

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
        syncButton.textContent = 'Starting Sync...';
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
                notification.success('Sync job started');
                window.location.reload();
            } else {
                notification.error('Failed to start sync job');
                syncButton.disabled = false;
                syncButton.textContent = 'Sync Database';
            }
        } catch (error) {
            notification.error('Error starting sync: ' + error.message);
            syncButton.disabled = false;
            syncButton.textContent = 'Sync Database';
        }
    };

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
            notification.error('Scan error: ' + record.message);
        }
    };

    const runScan = async () => {
        setStatus('Scanning the filesystem…');
        try {
            const response = await fetch(config.urls.utilities_index_photos_scan);
            if (!response.ok || !response.body) throw new Error('scan request failed');
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
                    if (line) handleRecord(JSON.parse(line));
                }
            }
            const tail = buffer.trim();
            if (tail) handleRecord(JSON.parse(tail));
        } catch (error) {
            setStatus('');
            notification.error('Failed to scan the filesystem.');
        }
    };

    if (syncButton) syncButton.addEventListener('click', startSync);
    if (canScan) runScan();

    return { runScan, startSync };
};
