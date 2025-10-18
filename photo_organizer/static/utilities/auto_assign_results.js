window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initAutoAssignResults = (jobId, personId, personName, config) => {
    const assignButton = document.getElementById('assign-selected-btn');
    const selectAllBtn = document.getElementById('select-all-btn');
    const deselectAllBtn = document.getElementById('deselect-all-btn');
    const selectedCountDisplay = document.getElementById('selected-count');
    const faceCheckboxes = document.querySelectorAll('.face-checkbox');
    const faceCards = document.querySelectorAll('.face-card');

    const updateSelectedCount = () => {
        const selectedCheckboxes = document.querySelectorAll('.face-checkbox:checked');
        const count = selectedCheckboxes.length;

        if (selectedCountDisplay) {
            selectedCountDisplay.textContent = count;
        }

        if (assignButton) {
            assignButton.disabled = count === 0;
        }
    };

    const handleCardClick = (event) => {
        if (event.target.classList.contains('face-checkbox')) {
            return;
        }

        const card = event.currentTarget;
        const checkbox = card.querySelector('.face-checkbox');

        if (checkbox) {
            checkbox.checked = !checkbox.checked;
            updateCardAppearance(card, checkbox.checked);
            updateSelectedCount();
        }
    };

    const updateCardAppearance = (card, isChecked) => {
        if (isChecked) {
            card.classList.add('selected');
        } else {
            card.classList.remove('selected');
        }
    };

    const handleCheckboxChange = (event) => {
        const checkbox = event.target;
        const card = checkbox.closest('.face-card');
        updateCardAppearance(card, checkbox.checked);
        updateSelectedCount();
    };

    const selectAll = () => {
        faceCheckboxes.forEach(checkbox => {
            checkbox.checked = true;
            const card = checkbox.closest('.face-card');
            updateCardAppearance(card, true);
        });
        updateSelectedCount();
    };

    const deselectAll = () => {
        faceCheckboxes.forEach(checkbox => {
            checkbox.checked = false;
            const card = checkbox.closest('.face-card');
            updateCardAppearance(card, false);
        });
        updateSelectedCount();
    };

    const assignSelected = async () => {
        const selectedCheckboxes = document.querySelectorAll('.face-checkbox:checked');
        const faceIds = Array.from(selectedCheckboxes).map(cb => parseInt(cb.value));

        if (faceIds.length === 0) {
            notification.warning('No faces selected');
            return;
        }

        assignButton.disabled = true;
        assignButton.textContent = 'Assigning...';

        try {
            const response = await fetch(config.urls.utilities_auto_assign_bulk, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    job_id: jobId,
                    person_id: personId,
                    face_ids: faceIds
                })
            });

            if (response.ok) {
                const data = await response.json();
                notification.success(data.message || `Assigned ${faceIds.length} faces to ${personName}`);
                setTimeout(() => {
                    window.location.href = config.buildUrl('person_faces', {person_id: personId});
                }, 1500);
            } else {
                const error = await response.json();
                notification.error(error.error || 'Failed to assign faces');
                assignButton.disabled = false;
                assignButton.textContent = 'Assign Selected Faces';
            }
        } catch (error) {
            notification.error('Error assigning faces: ' + error.message);
            assignButton.disabled = false;
            assignButton.textContent = 'Assign Selected Faces';
        }
    };

    faceCards.forEach(card => {
        card.addEventListener('click', handleCardClick);
        const checkbox = card.querySelector('.face-checkbox');
        if (checkbox && checkbox.checked) {
            updateCardAppearance(card, true);
        }
    });

    faceCheckboxes.forEach(checkbox => {
        checkbox.addEventListener('change', handleCheckboxChange);
    });

    if (selectAllBtn) {
        selectAllBtn.addEventListener('click', selectAll);
    }

    if (deselectAllBtn) {
        deselectAllBtn.addEventListener('click', deselectAll);
    }

    if (assignButton) {
        assignButton.addEventListener('click', assignSelected);
    }

    updateSelectedCount();

    return {
        selectAll,
        deselectAll,
        assignSelected,
        updateSelectedCount
    };
};