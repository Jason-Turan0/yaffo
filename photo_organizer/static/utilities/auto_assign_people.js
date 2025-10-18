window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initAutoAssignPeople = (people, unassignedCount, config) => {
    let pollInterval = null;

    const personSelect = document.getElementById('person-select');
    const similaritySlider = document.getElementById('similarity-slider');
    const similarityValue = document.getElementById('similarity-value');
    const startButton = document.getElementById('start-button');

    const updateSimilarityDisplay = () => {
        if (similarityValue) {
            similarityValue.textContent = parseFloat(similaritySlider.value).toFixed(2);
        }
    };

    const updateStartButtonState = () => {
        if (startButton) {
            startButton.disabled = !personSelect.value;
        }
    };

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

    const startAutoAssign = async () => {
        const personId = parseInt(personSelect.value);
        const similarityThreshold = parseFloat(similaritySlider.value);

        if (!personId) {
            notification.error('Please select a person');
            return;
        }

        startButton.disabled = true;
        startButton.textContent = 'Starting...';

        try {
            const response = await fetch(config.urls.utilities_auto_assign_start, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    person_id: personId,
                    similarity_threshold: similarityThreshold
                })
            });

            if (response.ok) {
                const data = await response.json();
                notification.success('Auto-assign job started');
                window.location.reload();
            } else {
                const error = await response.json();
                notification.error(error.error || 'Failed to start auto-assign job');
                startButton.disabled = false;
                startButton.textContent = 'Find Matching Faces';
            }
        } catch (error) {
            notification.error('Error starting auto-assign: ' + error.message);
            startButton.disabled = false;
            startButton.textContent = 'Find Matching Faces';
        }
    };

    const pollJobStatus = async () => {
        const jobCards = document.querySelectorAll('.job-card');

        for (const card of jobCards) {
            const jobId = card.dataset.jobId;

            try {
                const response = await fetch(config.buildUrl('utilities_get_job_status', {job_id: jobId}));

                if (response.ok) {
                    const job = await response.json();

                    card.querySelector('.job-status').textContent = job.status;
                    const totalCount = job.completed_count + job.error_count + job.cancelled_count;
                    const jobProgress = totalCount && job.task_count ? (totalCount / job.task_count) * 100 : 0;
                    card.querySelector('.job-message').textContent = (job.message || 'Processing...').replace('{totalCount}', totalCount).replace('{taskCount}', job.task_count);
                    card.querySelector('.progress-bar').style.width = jobProgress + '%';
                    card.querySelector('.progress-text').textContent = jobProgress.toFixed(2) + '%';

                    const cancelBtn = card.querySelector('.cancel-job-btn');
                    if (job.status === 'COMPLETED' || job.status === 'FAILED' || job.status === 'CANCELLED') {
                        if (cancelBtn) {
                            cancelBtn.disabled = true;
                        }

                        if (pollInterval) {
                            clearInterval(pollInterval);
                            pollInterval = null;
                        }

                        if (job.status === 'COMPLETED') {
                            notification.success('Auto-assign completed');
                        } else if (job.status === 'CANCELLED') {
                            notification.warning('Auto-assign was cancelled');
                            setTimeout(() => window.location.reload(), 2000);
                        } else {
                            notification.error('Auto-assign failed: ' + (job.error || 'Unknown error'));
                        }
                    }
                }
            } catch (error) {
                console.error('Error polling job status:', error);
            }
        }
    };

    if (personSelect) {
        personSelect.addEventListener('change', updateStartButtonState);
    }

    if (similaritySlider) {
        similaritySlider.addEventListener('input', updateSimilarityDisplay);
        updateSimilarityDisplay();
    }

    if (startButton) {
        startButton.addEventListener('click', startAutoAssign);
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
        startAutoAssign,
        pollJobStatus,
        cancelJob
    };
};