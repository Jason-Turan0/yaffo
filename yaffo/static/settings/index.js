window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

window.PHOTO_ORGANIZER.initSettings = (initialMediaDirs, config) => {
    let mediaDirs = [...initialMediaDirs];

    const addMediaDir = async () => {
        const input = document.getElementById('new-media-dir');
        const directory = input.value.trim();

        if (!directory) {
            window.notification.error('Please enter a directory path');
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
                window.notification.success('Media directory added successfully');
            } else {
                window.notification.error(data.error || 'Failed to add directory');
            }
        } catch (error) {
            console.error('Error adding media directory:', error);
            window.notification.error('Failed to add directory');
        }
    };

    const removeMediaDir = async (index) => {
        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: 'Remove Media Directory',
            message: `Remove directory: ${mediaDirs[index]}?`,
            confirmText: 'Remove',
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
                window.notification.success(`Removed: ${data.removed}`);
            } else {
                window.notification.error(data.error || 'Failed to remove directory');
            }
        } catch (error) {
            console.error('Error removing media directory:', error);
            window.notification.error('Failed to remove directory');
        }
    };

    const renderMediaDirs = () => {
        const container = document.getElementById('media-dirs-list');

        if (mediaDirs.length === 0) {
            container.innerHTML = '<p class="no-data">No media directories configured</p>';
            return;
        }

        container.innerHTML = mediaDirs.map((dir, index) => `
            <div class="media-dir-item" data-index="${index}">
                <span class="media-dir-path">${dir}</span>
                <button type="button" class="btn-danger btn-sm" onclick="window.PHOTO_ORGANIZER.settings.removeMediaDir(${index})">Remove</button>
            </div>
        `).join('');
    };

    const changeThumbnailDir = async () => {
        const input = document.getElementById('new-thumbnail-dir');
        const directory = input.value.trim();

        if (!directory) {
            window.notification.error('Please enter a directory path');
            return;
        }

        // Get current stats before showing confirmation
        try {
            const statsResponse = await fetch(config.urls.get_thumbnail_stats_api);
            const stats = await statsResponse.json();

            if (!statsResponse.ok) {
                window.notification.error('Failed to get thumbnail stats');
                return;
            }

            // Show confirmation dialog
            const message = `Are you sure you want to move the thumbnail directory?\n\n` +
                           `Current location: ${stats.directory}\n` +
                           `New location: ${directory}\n\n` +
                           `This will move ${stats.count} files (${stats.size_formatted})`;

            const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
                title: 'Move Thumbnail Directory',
                message: message,
                confirmText: 'Move',
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
                window.notification.success(`Moved ${data.files_moved} files (${data.size_moved}) to ${data.new_directory}`);

                // Update UI
                document.getElementById('current-thumbnail-dir').textContent = data.new_directory;
                document.getElementById('thumbnail-stats').innerHTML =
                    `<span class="stat-item">Files: <strong>${data.files_moved}</strong></span>
                     <span class="stat-item">Total Size: <strong>${data.size_moved}</strong></span>`;
                input.value = '';
            } else {
                window.notification.error(data.error || 'Failed to move thumbnails');
            }
        } catch (error) {
            console.error('Error changing thumbnail directory:', error);
            window.notification.error('Failed to change thumbnail directory');
        }
    };

    return {
        addMediaDir,
        removeMediaDir,
        changeThumbnailDir
    };
};