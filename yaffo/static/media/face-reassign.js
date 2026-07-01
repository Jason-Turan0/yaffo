// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.VIEW_PHOTO = window.PHOTO_ORGANIZER.VIEW_PHOTO || {};
/**
 * @param {MediaFacePerson[]} allPeople
 * @param {I18nService} i18n
 * @param {AppConfig} config
 * @returns {FaceReassignApi}
 */
window.PHOTO_ORGANIZER.VIEW_PHOTO.initFaceReassign = (allPeople, i18n, config) => {
    /**
     * @param {HTMLElement} faceThumbnail
     * @param {number} faceId
     */
    const createReassignOverlay = (faceThumbnail, faceId) => {
        if (!window.PHOTO_ORGANIZER.COMPONENTS.overlay) {
            throw new Error('Overlay component is not initialized');
        }
        const overlayContent = `
            <div class="face-reassign-header">${i18n.t('media:faces.reassign')}</div>
            <div class="face-reassign-controls">
                <select id="reassign-person-select-${faceId}" class="searchable-select face-reassign-select">
                    <option value="">${i18n.t('media:faces.selectPerson')}</option>
                    ${allPeople.map(person =>
            `<option value="${person.id}">${person.name}</option>`
        ).join('')}
                </select>
                <div class="face-reassign-actions">
                    <button class="btn btn-secondary btn-sm" data-action="cancel">
                        ${i18n.t('common:cancel')}
                    </button>
                    <button class="btn btn-primary btn-sm" data-action="apply">
                        ${i18n.t('common:apply')}
                    </button>
                </div>
            </div>
        `;
        const {overlay, close} = window.PHOTO_ORGANIZER.COMPONENTS.overlay.init(
            faceThumbnail.id,
            overlayContent,
            {placement: 'right', closeOnOutsideClick: false}
        )

        const selectElement = overlay.querySelector(`#reassign-person-select-${faceId}`);
        if (!(selectElement instanceof HTMLSelectElement)) return overlay;
        window.SearchableSelect?.init(selectElement);

        const cancelBtn = overlay.querySelector('[data-action="cancel"]');
        const applyBtn = overlay.querySelector('[data-action="apply"]');
        if (!(cancelBtn instanceof HTMLButtonElement) || !(applyBtn instanceof HTMLButtonElement)) return overlay;

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

    /**
     * @param {number} faceId
     * @param {string} personId
     * @param {HTMLButtonElement} applyBtn
     */
    const reassignFace = async (faceId, personId, applyBtn) => {
        if (!personId) {
            window.notification.warning(i18n.t('media:faces.selectPersonRequired'));
            return;
        }

        applyBtn.disabled = true;
        applyBtn.textContent = i18n.t('media:faces.applying');

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
                window.notification.success(data.message || i18n.t('media:faces.reassignSucceeded'));
                setTimeout(() => {
                    window.location.reload();
                }, 500);
            } else {
                window.notification.error(data.message || i18n.t('media:faces.reassignFailed'));
                applyBtn.disabled = false;
                applyBtn.textContent = i18n.t('common:apply');
            }
        } catch (error) {
            console.error('Error reassigning face:', error);
            window.notification.error(i18n.t('media:faces.reassignError'));
            applyBtn.disabled = false;
            applyBtn.textContent = i18n.t('common:apply');
        }
    };

    const handleFaceClick = (/** @type {Event} */ e) => {
        if (!(e.currentTarget instanceof HTMLElement)) return;
        createReassignOverlay(e.currentTarget, parseInt(e.currentTarget.dataset.faceId || '', 10));
    };


    document.querySelectorAll('.face-thumbnail').forEach(thumbnail => {
        thumbnail.addEventListener('click', handleFaceClick);
    });

    return {};
};
