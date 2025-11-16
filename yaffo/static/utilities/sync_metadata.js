window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initSyncMetadata = (photosToSync, config) => {
    const syncButton = document.getElementById('sync-button');

    const startSync = async () => {
        if (!photosToSync || photosToSync.length === 0) {
            notification.warning('No photos to sync');
            return;
        }

        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: 'Sync Metadata to Files',
            message: `This will write metadata to ${photosToSync.length} file(s).\n\nThis operation will modify the image files on disk.\n\nAre you sure you want to continue?`,
            confirmText: 'Yes, Sync Metadata',
            cancelText: 'Cancel',
            confirmClass: 'btn-primary'
        });

        if (!confirmed) {
            return;
        }

        syncButton.disabled = true;
        syncButton.textContent = 'Starting sync...';

        try {
            const response = await fetch(config.urls.utilities_sync_metadata_start, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    photo_ids: photosToSync.map(p => p.photo_id)
                })
            });

            if (response.ok) {
                const data = await response.json();
                notification.success('Metadata sync job started');
                window.location.reload();
            } else {
                const error = await response.json();
                notification.error(error.error || 'Failed to start metadata sync');
                syncButton.disabled = false;
                syncButton.textContent = 'Sync Metadata to Files';
            }
        } catch (error) {
            notification.error('Error starting metadata sync: ' + error.message);
            syncButton.disabled = false;
            syncButton.textContent = 'Sync Metadata to Files';
        }
    };

    if (syncButton) {
        syncButton.addEventListener('click', startSync);
    }

    return {
        startSync,
    };
};