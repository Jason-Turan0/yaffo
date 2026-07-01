// @ts-check

/**
 * A configured media directory row.
 * @typedef {Object} SettingsMediaDir
 * @property {string} path
 *
 * A record from the thumbnail-stats NDJSON stream (see streamThumbnailStats).
 * @typedef {{ type: 'progress', scanned: number }} ThumbnailProgressRecord
 * @typedef {{ type: 'done', count: number, size: number }} ThumbnailDoneRecord
 * @typedef {{ type: 'error', message?: string }} ThumbnailErrorRecord
 * @typedef {ThumbnailProgressRecord | ThumbnailDoneRecord | ThumbnailErrorRecord} ThumbnailStatsRecord
 *
 * The public surface returned by the page initializer.
 * @typedef {Object} SettingsApi
 * @property {() => Promise<void>} addMediaDir
 * @property {(index: number) => Promise<void>} removeMediaDir
 * @property {() => Promise<void>} changeThumbnailDir
 * @property {() => Promise<void>} streamThumbnailStats
 */

const settingsWindow = window;

settingsWindow.PHOTO_ORGANIZER = settingsWindow.PHOTO_ORGANIZER || {};
const settings = settingsWindow.PHOTO_ORGANIZER.settings =
    /** @type {SettingsNamespace} */ (settingsWindow.PHOTO_ORGANIZER.settings || {});

/**
 * @param {SettingsMediaDir[]} initialMediaDirs
 * @param {I18nService} i18n
 * @param {AppConfig} config
 * @returns {SettingsApi}
 */
settings.init = (initialMediaDirs, i18n, config) => {
    let mediaDirs = [...initialMediaDirs];

    /** @param {unknown} value */
    const escapeHtml = (value) => {
        const element = document.createElement('div');
        element.textContent = String(value);
        return element.innerHTML;
    };

    /** @param {number} bytes */
    const formatBytes = (bytes) => {
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let value = Number(bytes);
        let unitIndex = 0;
        while (value >= 1024 && unitIndex < units.length - 1) {
            value /= 1024;
            unitIndex += 1;
        }
        return i18n.t('settings:thumbnail.size', {
            value: i18n.number(value, { maximumFractionDigits: 2 }),
            unit: units[unitIndex]
        });
    };

    const addMediaDir = async () => {
        const input = /** @type {HTMLInputElement} */ (document.getElementById('new-media-dir'));
        const directory = input.value.trim();

        if (!directory) {
            settingsWindow.notification.error(i18n.t('settings:directory.pathRequired'));
            return;
        }

        try {
            const response = await fetch(config.urls.add_media_dir, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ directory })
            });

            const data = await response.json();

            if (response.ok) {
                mediaDirs = data.media_dirs;
                renderMediaDirs();
                input.value = '';
                settingsWindow.notification.success(i18n.t('settings:media.addSucceeded'));
            } else {
                settingsWindow.notification.error(data.error || i18n.t('settings:media.addFailed'));
            }
        } catch (error) {
            console.error('Error adding media directory:', error);
            settingsWindow.notification.error(i18n.t('settings:media.addFailed'));
        }
    };

    /** @param {number} index */
    const removeMediaDir = async (index) => {
        const directory = mediaDirs[index];
        if (!directory) {
            return;
        }
        const confirmed = await settingsWindow.PHOTO_ORGANIZER.confirmDialog({
            title: i18n.t('settings:media.removeTitle'),
            message: i18n.t('settings:media.removeMessage', { directory: directory.path }),
            confirmText: i18n.t('settings:media.removeConfirm'),
            confirmClass: 'btn-danger'
        });

        if (!confirmed) {
            return;
        }

        try {
            const url = config.buildUrl('remove_media_dir', { index });
            const response = await fetch(url, {
                method: 'DELETE'
            });

            const data = await response.json();

            if (response.ok) {
                mediaDirs = data.media_dirs;
                renderMediaDirs();
                settingsWindow.notification.success(i18n.t('settings:media.removeSucceeded', {
                    directory: data.removed
                }));
            } else {
                settingsWindow.notification.error(data.error || i18n.t('settings:media.removeFailed'));
            }
        } catch (error) {
            console.error('Error removing media directory:', error);
            settingsWindow.notification.error(i18n.t('settings:media.removeFailed'));
        }
    };

    const renderMediaDirs = () => {
        const container = document.getElementById('media-dirs-list');
        if (!container) return;

        if (mediaDirs.length === 0) {
            container.innerHTML = `<p class="no-data">${i18n.t('settings:media.noneConfigured')}</p>`;
            return;
        }

        container.innerHTML = mediaDirs.map((dir, index) => `
            <div class="media-dir-item" data-index="${index}">
                <span class="media-dir-path">${escapeHtml(dir.path)}</span>
                <button type="button" class="btn btn-danger btn-sm"
                        data-action="remove-media-dir" data-index="${index}">
                    ${i18n.t('settings:media.removeConfirm')}
                </button>
            </div>
        `).join('');
    };

    const changeThumbnailDir = async () => {
        const input = /** @type {HTMLInputElement} */ (document.getElementById('new-thumbnail-dir'));
        const directory = input.value.trim();

        if (!directory) {
            settingsWindow.notification.error(i18n.t('settings:directory.pathRequired'));
            return;
        }

        // Get current stats before showing confirmation
        try {
            const statsResponse = await fetch(config.urls.get_thumbnail_stats_api);
            const stats = await statsResponse.json();

            if (!statsResponse.ok) {
                settingsWindow.notification.error(stats.error || i18n.t('settings:thumbnail.statsFailed'));
                return;
            }

            const message = i18n.t('settings:thumbnail.moveMessage', {
                currentDirectory: stats.directory,
                newDirectory: directory,
                count: stats.count,
                formattedCount: i18n.number(stats.count),
                size: formatBytes(stats.size)
            });

            const confirmed = await settingsWindow.PHOTO_ORGANIZER.confirmDialog({
                title: i18n.t('settings:thumbnail.moveTitle'),
                message: message,
                confirmText: i18n.t('settings:thumbnail.moveConfirm'),
                confirmClass: 'btn-primary'
            });

            if (!confirmed) {
                return;
            }

            // Call API to move thumbnails
            const response = await fetch(config.urls.update_thumbnail_dir, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ directory })
            });

            const data = await response.json();

            if (response.ok) {
                settingsWindow.notification.success(i18n.t('settings:thumbnail.moveSucceeded', {
                    count: data.files_moved,
                    formattedCount: i18n.number(data.files_moved),
                    size: formatBytes(data.size_moved),
                    directory: data.new_directory
                }));

                window.location.reload();
            } else {
                settingsWindow.notification.error(data.error || i18n.t('settings:thumbnail.moveFailed'));
            }
        } catch (error) {
            console.error('Error changing thumbnail directory:', error);
            settingsWindow.notification.error(i18n.t('settings:thumbnail.moveFailed'));
        }
    };

    // Fill the thumbnail stats live from the NDJSON stream so the page doesn't block on
    // the (slow) recursive walk. `progress` records drive the running file count; the
    // final `done` record sets the count + formatted size. Mirrors index_photos.js.
    /** @param {ThumbnailStatsRecord} record */
    const handleStatsRecord = (record) => {
        const countEl = document.getElementById('thumbnail-count');
        const sizeEl = document.getElementById('thumbnail-size');
        if (record.type === 'progress') {
            if (countEl) countEl.textContent = i18n.number(Number(record.scanned));
        } else if (record.type === 'done') {
            if (countEl) countEl.textContent = i18n.number(Number(record.count));
            if (sizeEl) sizeEl.textContent = formatBytes(record.size);
        } else if (record.type === 'error') {
            if (sizeEl) sizeEl.textContent = '—';
            settingsWindow.notification.error(record.message || i18n.t('settings:thumbnail.countFailed'));
        }
    };

    const streamThumbnailStats = async () => {
        if (!document.getElementById('thumbnail-stats')) return;
        try {
            const response = await fetch(config.urls.settings_thumbnail_stats_stream);
            if (!response.ok || !response.body) throw new Error('Thumbnail stats request failed');
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
                    if (line) handleStatsRecord(JSON.parse(line));
                }
            }
            const tail = buffer.trim();
            if (tail) handleStatsRecord(JSON.parse(tail));
        } catch (error) {
            const sizeEl = document.getElementById('thumbnail-size');
            if (sizeEl) sizeEl.textContent = '—';
            settingsWindow.notification.error(i18n.t('settings:thumbnail.countFailed'));
        }
    };

    document.querySelector('.main-content')?.addEventListener('click', (event) => {
        const origin = event.target instanceof Element ? event.target : null;
        const button = /** @type {HTMLElement | null} */ (origin?.closest('[data-action]') ?? null);
        if (!button) {
            return;
        }
        if (button.dataset.action === 'add-media-dir') {
            addMediaDir();
        } else if (button.dataset.action === 'remove-media-dir') {
            removeMediaDir(Number(button.dataset.index));
        } else if (button.dataset.action === 'change-thumbnail-dir') {
            changeThumbnailDir();
        }
    });

    streamThumbnailStats();

    return {
        addMediaDir,
        removeMediaDir,
        changeThumbnailDir,
        streamThumbnailStats
    };
};
