window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initIndexPhotos = (unindexedPhotos, orphanedPhotos) => {
    const cancelJob = async (jobId) => {
        try {
            const response = await fetch(APP_CONFIG.buildUrl('utilities_cancel_job', {job_id: jobId}), {
                method: 'POST'
            });

            if (response.ok) {
                notification.success('Cancellation requested');
            } else {
                notification.error('Failed to cancel job');
            }
        } catch (error) {
            notification.error('Error cancelling job: ' + error.message);
        }
    };

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
                startPolling(data.job_id);
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
    }

    const startPolling = (jobId) => {
        window.location.reload();
    }

    const pollJobStatus = async () => {
        const jobCards = document.querySelectorAll('.job-card');

        for (const card of jobCards) {
            const jobId = card.dataset.jobId;

            try {
                const response = await fetch(APP_CONFIG.buildUrl('utilities_get_job_status', {job_id: jobId}));

                if (response.ok) {
                    const job = await response.json();

                    card.querySelector('.job-status').textContent = job.status;
                    card.querySelector('.job-message').textContent = job.message || 'Processing...';
                    card.querySelector('.progress-bar').style.width = job.progress + '%';
                    card.querySelector('.progress-text').textContent = job.progress + '%';

                    const cancelBtn = card.querySelector('.cancel-job-btn');
                    if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
                        if (cancelBtn) {
                            cancelBtn.disabled = true;
                        }

                        if (pollInterval) {
                            clearInterval(pollInterval);
                            pollInterval = null;
                        }

                        if (job.status === 'completed') {
                            notification.success('Sync completed successfully');
                            setTimeout(() => window.location.reload(), 2000);
                        } else if (job.status === 'cancelled') {
                            notification.warning('Sync was cancelled');
                            setTimeout(() => window.location.reload(), 2000);
                        } else {
                            notification.error('Sync failed: ' + (job.error || 'Unknown error'));
                        }
                    }
                }
            } catch (error) {
                console.error('Error polling job status:', error);
            }
        }
    }

    const syncButton = document.getElementById('sync-button');
    if (syncButton) {
        syncButton.addEventListener('click', startSync);
    }

    const cancelButtons = document.querySelectorAll('.cancel-job-btn');
    cancelButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            const jobId = e.target.dataset.jobId;
            cancelJob(jobId);
        });
    });

    if (document.querySelectorAll('.job-card').length > 0) {
        pollInterval = setInterval(pollJobStatus, 1000);
        pollJobStatus();
    }

    return {
        startSync,
        startPolling,
        pollJobStatus,
        cancelJob
    }
}




