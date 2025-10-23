window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initIndexPhotos = (unindexedPhotos, orphanedPhotos) => {
    const jobProgress = window.PHOTO_ORGANIZER.COMPONENTS.jobProgress.init(window.APP_CONFIG, {
        onComplete: (job) => {
            setTimeout(() => window.location.reload(), 2000);
        },
        onCancel: (job) => {
            setTimeout(() => window.location.reload(), 2000);
        },
        onError: (job) => {}
    });

    const startSync = async () => {
        const syncButton = document.getElementById('sync-button');
        syncButton.disabled = true;
        syncButton.textContent = 'Starting Sync...';

        try {
            const response = await fetch(APP_CONFIG.urls.utilities_sync_photos, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    files_to_index: unindexedPhotos.map(p => p.full_path),
                    files_to_delete: orphanedPhotos.map(p => p.id)
                })
            });

            if (response.ok) {
                const data = await response.json();
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

    const syncButton = document.getElementById('sync-button');
    if (syncButton) {
        syncButton.addEventListener('click', startSync);
    }

    return {
        startSync,
        jobProgress
    };
};




