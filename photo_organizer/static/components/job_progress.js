window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};

window.PHOTO_ORGANIZER.COMPONENTS.jobProgress = {
    init: (config, options = {}) => {
        let pollInterval = null;

        const {
            onComplete = () => {
            },
            onCancel = () => {
            },
            onError = () => {
            },
            hasResults = false,
            onShowResults = () => {
            },
        } = options;

        const cancelJob = async (jobId) => {
            try {
                const response = await fetch(config.buildUrl('utilities_cancel_job', {job_id: jobId}), {
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

        const deleteJob = async (jobId) => {
            try {
                const response = await fetch(config.buildUrl('utilities_delete_job', {job_id: jobId}), {
                    method: 'POST'
                });

                if (response.ok) {
                    notification.success('Job deleted');
                    const jobCard = document.querySelectorAll('.job-card').find(card => card.dataset.jobId === jobId);
                    if (jobCard) {
                        jobCard.remove();
                    }

                } else {
                    notification.error('Failed to delete job');
                }
            } catch (error) {
                notification.error('Error deleting job: ' + error.message);
            }
        }

        const isFinishedStatus = (status) => {
            return status.toUpperCase() === 'COMPLETED' || status.toUpperCase() === 'FAILED' || status.toUpperCase() === 'CANCELLED'
        }

        const pollJobStatus = async () => {
            const jobCards = document.querySelectorAll('.job-card');
            let finishedCount = 0;
            for (const card of jobCards) {
                const jobId = card.dataset.jobId;

                try {
                    const response = await fetch(config.buildUrl('utilities_get_job_status', {job_id: jobId}));

                    if (response.ok) {
                        const job = await response.json();

                        card.querySelector('.job-status').textContent = job.status;
                        const totalCount = job.completed_count + job.error_count + job.cancelled_count;
                        const jobProgress = totalCount && job.task_count ? (totalCount / job.task_count) * 100 : 0;
                        card.querySelector('.job-message').textContent = (job.message || 'Processing...')
                            .replace('{totalCount}', totalCount)
                            .replace('{taskCount}', job.task_count);
                        card.querySelector('.progress-bar').style.width = jobProgress + '%';
                        card.querySelector('.progress-text').textContent = jobProgress.toFixed(2) + '%';

                        const cancelBtn = card.querySelector('.cancel-job-btn');
                        const showResultsBtn = card.querySelector('.show-job-results-btn ');

                        const statusUpper = job.status.toUpperCase();
                        cancelBtn.dataset.status = statusUpper;
                        if (isFinishedStatus(statusUpper)) {
                            cancelBtn.textContent = 'Delete';
                            finishedCount +=1;
                            if (statusUpper === 'COMPLETED') {
                                onComplete(job);
                                if (hasResults) {
                                    showResultsBtn.classList.remove('job-btn-hidden');
                                }
                            } else if (statusUpper === 'CANCELLED') {
                                onCancel(job);
                            } else {
                                onError(job);
                            }
                        } else {
                            cancelBtn.textContent = 'Cancel';
                        }
                    }
                } catch (error) {
                    console.error('Error polling job status:', error);
                }
            }
            if(finishedCount === jobCards.length && pollInterval) {
                clearInterval(pollInterval);
            }
        };

        const setupCancelButtons = () => {
            const cancelButtons = document.querySelectorAll('.cancel-job-btn');
            cancelButtons.forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const jobId = e.target.dataset.jobId;
                    const status = e.target.dataset.status;
                    if (isFinishedStatus(status)) {
                        deleteJob(jobId);
                    } else {
                        cancelJob(jobId);
                    }
                });
            });
        };

        const setupShowResultButtons = () => {
            const showResultButtons = document.querySelectorAll('.show-job-results-btn');
            showResultButtons.forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const jobId = e.target.dataset.jobId;
                    onShowResults(jobId)
                });
            });
        };

        const startPolling = () => {
            if (document.querySelectorAll('.job-card').length > 0) {
                pollInterval = setInterval(pollJobStatus, 1000);
                pollJobStatus();
            }
        };

        const destroy = () => {
            if (pollInterval) {
                clearInterval(pollInterval);
                pollInterval = null;
            }
        };

        setupCancelButtons();
        setupShowResultButtons();
        startPolling();

        return {
            cancelJob,
            pollJobStatus,
            startPolling,
            destroy
        };
    }
};