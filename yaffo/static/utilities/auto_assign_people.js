window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initAutoAssignPeople = (people, unassignedCount, config) => {
    const jobProgress = window.PHOTO_ORGANIZER.COMPONENTS.jobProgress.init(config, {
        onComplete: (job) => {},
        onCancel: (job) => {
            setTimeout(() => window.location.reload(), 2000);
        },
        onError: (job) => {},
        hasResults: true,
        pollingInterval: 5000,
        onShowResults: (jobId) => {
             window.location.href = config.buildUrl('utilities_auto_assign_results', {job_id: jobId});
        }
    });

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

    return {
        startAutoAssign,
        jobProgress
    };
};