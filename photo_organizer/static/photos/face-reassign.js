window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.VIEW_PHOTO = window.PHOTO_ORGANIZER.VIEW_PHOTO || {};
window.PHOTO_ORGANIZER.VIEW_PHOTO.initFaceReassign = (allPeople, config) => {
    const createReassignOverlay = (faceThumbnail, faceId) => {
        const overlayContent = `
            <div class="face-reassign-header">Reassign Face</div>
            <div class="face-reassign-controls">
                <select id="reassign-person-select-${faceId}" class="searchable-select face-reassign-select">
                    <option value="">Select a person...</option>
                    ${allPeople.map(person =>
            `<option value="${person.id}">${person.name}</option>`
        ).join('')}
                </select>
                <div class="face-reassign-actions">
                    <button class="face-reassign-btn face-reassign-btn-cancel" data-action="cancel">
                        Cancel
                    </button>
                    <button class="face-reassign-btn face-reassign-btn-apply" data-action="apply">
                        Apply
                    </button>
                </div>
            </div>
        `;
        const {overlay, close} = window.PHOTO_ORGANIZER.COMPONENTS.overlay.init(
            faceThumbnail.id,
            overlayContent,
            {placement: 'right'}
        )

        const selectElement = overlay.querySelector(`#reassign-person-select-${faceId}`);
        window.SearchableSelect.init(selectElement);

        const cancelBtn = overlay.querySelector('[data-action="cancel"]');
        const applyBtn = overlay.querySelector('[data-action="apply"]');

        cancelBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            close();
        });

        applyBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            await reassignFace(faceId, selectElement.value, applyBtn);
        });

        return overlay;
    };

    const reassignFace = async (faceId, personId, applyBtn) => {
        if (!personId) {
            window.notification.warning('Please select a person');
            return;
        }

        applyBtn.disabled = true;
        applyBtn.textContent = 'Applying...';
        debugger

        try {
            const response = await fetch(config.urls.faces_assign, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({
                    faces: [faceId],
                    person: personId,
                    faceStatus: 'ASSIGNED'
                })
            });

            const data = await response.json();

            if (response.ok && data.success) {
                window.notification.success(data.message || 'Face reassigned successfully');
                setTimeout(() => {
                    window.location.reload();
                }, 500);
            } else {
                window.notification.error(data.message || 'Failed to reassign face');
                applyBtn.disabled = false;
                applyBtn.textContent = 'Apply';
            }
        } catch (error) {
            console.error('Error reassigning face:', error);
            window.notification.error('An error occurred while reassigning the face');
            applyBtn.disabled = false;
            applyBtn.textContent = 'Apply';
        }
    };

    const handleFaceClick = (e) => createReassignOverlay(e.currentTarget, parseInt(e.currentTarget.dataset.faceId))


    document.querySelectorAll('.face-thumbnail').forEach(thumbnail => {
        thumbnail.addEventListener('click', handleFaceClick);
    });

    return {};
};