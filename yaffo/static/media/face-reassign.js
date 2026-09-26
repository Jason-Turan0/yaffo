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
     * @typedef {{faceId: number, overlay: HTMLElement, close: () => void}} OpenReassign
     */

    /**
     * The overlay currently on screen, if any. Without this every press built
     * another copy: three presses on one face left three stacked overlays, all
     * carrying the same `reassign-person-select-<id>` element id, so
     * getElementById and SearchableSelect resolved to the wrong one.
     * @type {OpenReassign | null}
     */
    let openReassign = null;

    /**
     * @param {HTMLElement} faceThumbnail
     * @param {number} faceId
     * @returns {OpenReassign}
     */
    const createReassignOverlay = (faceThumbnail, faceId) => {
        if (!window.PHOTO_ORGANIZER.COMPONENTS.overlay) {
            throw new Error('Overlay component is not initialized');
        }
        // Clear only means something for a face that has a person, or was ignored.
        const status = faceThumbnail.dataset.faceStatus || '';
        const canClear = status !== '' && status !== 'UNASSIGNED';
        const clearButton = canClear
            ? `<button class="btn btn-secondary btn-sm face-reassign-clear" data-action="clear"
                    title="${i18n.t('media:faces.clearHint')}">${i18n.t('media:faces.clear')}</button>`
            : '';
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
                    ${clearButton}
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

        const record = {faceId, overlay, close};

        const selectElement = overlay.querySelector(`#reassign-person-select-${faceId}`);
        if (!(selectElement instanceof HTMLSelectElement)) return record;
        window.SearchableSelect?.init(selectElement);

        const cancelBtn = overlay.querySelector('[data-action="cancel"]');
        const applyBtn = overlay.querySelector('[data-action="apply"]');
        if (!(cancelBtn instanceof HTMLButtonElement) || !(applyBtn instanceof HTMLButtonElement)) return record;

        cancelBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            close();
        });

        applyBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            await reassignFace(faceId, selectElement.value, applyBtn);
        });

        const clearBtn = overlay.querySelector('[data-action="clear"]');
        if (clearBtn instanceof HTMLButtonElement) {
            clearBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                await clearFace(faceId, clearBtn);
            });
        }

        return record;
    };

    /**
     * Take the person off this face (or undo an ignore), leaving it unassigned.
     * @param {number} faceId
     * @param {HTMLButtonElement} clearBtn
     */
    const clearFace = async (faceId, clearBtn) => {
        clearBtn.disabled = true;
        clearBtn.textContent = i18n.t('media:faces.clearing');
        try {
            const response = await fetch(config.urls.faces_unassign, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({ faces: [faceId] })
            });
            const data = await response.json();
            if (response.ok && data.success) {
                window.notification.success(data.message || i18n.t('media:faces.clearSucceeded'));
                setTimeout(() => {
                    window.location.reload();
                }, 500);
                return;
            }
            window.notification.error(data.message || i18n.t('media:faces.clearFailed'));
        } catch (error) {
            console.error('Error clearing face:', error);
            window.notification.failure(i18n.t('media:faces.clearFailed'));
        }
        clearBtn.disabled = false;
        clearBtn.textContent = i18n.t('media:faces.clear');
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
            window.notification.failure(i18n.t('media:faces.reassignError'));
            applyBtn.disabled = false;
            applyBtn.textContent = i18n.t('common:apply');
        }
    };

    const handleFaceClick = (/** @type {Event} */ e) => {
        if (!(e.currentTarget instanceof HTMLElement)) return;
        const faceId = parseInt(e.currentTarget.dataset.faceId || '', 10);

        /* A closing overlay is still in the DOM until its transition ends, and
           Esc closes one without going through us, so read the node rather
           than trusting the slot. */
        const current = openReassign;
        const isOpen = current !== null
            && current.overlay.isConnected
            && !current.overlay.classList.contains('closing');
        const pressedTheOpenFace = isOpen && current.faceId === faceId;

        if (isOpen && current) current.close();
        openReassign = null;

        // A second press on the same face dismisses it.
        if (pressedTheOpenFace) return;

        openReassign = createReassignOverlay(e.currentTarget, faceId);
    };


    document.querySelectorAll('.face-thumbnail').forEach(thumbnail => {
        thumbnail.addEventListener('click', handleFaceClick);
    });

    return {};
};
