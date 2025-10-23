window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initDiscoverPeople = (unassignedCount, config) => {
    const jobProgress = window.PHOTO_ORGANIZER.COMPONENTS.jobProgress.init(config, {
        onComplete: (job) => {},
        onCancel: (job) => {
            setTimeout(() => window.location.reload(), 2000);
        },
        onError: (job) => {},
        hasResults: true,
        pollingInterval: 1000,
        onShowResults: (jobId) => {
             window.location.href = config.buildUrl('utilities_discover_people_results', {job_id: jobId});
        }
    });

    const distanceSlider = document.getElementById('distance-slider');
    const distanceValue = document.getElementById('distance-value');
    const startButton = document.getElementById('start-button');

    const updateDistanceDisplay = () => {
        if (distanceValue) {
            distanceValue.textContent = distanceSlider.value;
        }
    };

    const startDiscoverPeople = async () => {
        const distance = parseInt(distanceSlider.value);

        startButton.disabled = true;
        startButton.textContent = 'Starting...';

        try {
            const response = await fetch(config.urls.utilities_discover_people_start, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    distance_threshold: distance
                })
            });

            if (response.ok) {
                const data = await response.json();
                notification.success('Discover people job started');
                window.location.reload();
            } else {
                const error = await response.json();
                notification.error(error.error || 'Failed to start discover people job');
                startButton.disabled = false;
                startButton.textContent = 'Discover People';
            }
        } catch (error) {
            notification.error('Error starting discover people: ' + error.message);
            startButton.disabled = false;
            startButton.textContent = 'Discover People';
        }
    };

    if (distanceSlider) {
        distanceSlider.addEventListener('input', updateDistanceDisplay);
        updateDistanceDisplay();
    }

    if (startButton) {
        startButton.addEventListener('click', startDiscoverPeople);
    }

    return {
        startDiscoverPeople,
        jobProgress
    };
};