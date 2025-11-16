window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initAutoAssignPeople = (people, unassignedCount, config) => {
    const personSelect = document.getElementById('person-select');
    const startButton = document.getElementById('start-button');

    const updateStartButtonState = () => {
        if (startButton) {
            startButton.disabled = !personSelect.value;
        }
    };

    const startAutoAssign = async () => {
        const personId = parseInt(personSelect.value);
        const similarityElement = document.getElementById('similarity-range');
        const similarityValue = parseFloat(similarityElement.value);
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
                    similarity_threshold: similarityValue
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

    if (startButton) {
        startButton.addEventListener('click', startAutoAssign);
    }

    return {
        startAutoAssign
    };
};