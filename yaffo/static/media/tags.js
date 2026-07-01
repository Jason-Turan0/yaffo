// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.VIEW_PHOTO = window.PHOTO_ORGANIZER.VIEW_PHOTO || {};
/**
 * @param {number} photoId
 * @param {MediaTag[]} initialTags
 * @param {I18nService} i18n
 * @param {AppConfig} config
 * @returns {PhotoTagsApi}
 */
window.PHOTO_ORGANIZER.VIEW_PHOTO.initPhotoTags = (photoId, initialTags, i18n, config) => {
    const modal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('tagsModal');

    /** @type {MediaTag[]} */
    let tags = [];
    let nextTempId = -1;

    /**
     * @param {string} id
     * @returns {HTMLInputElement}
     */
    const getInput = (id) => {
        const input = document.getElementById(id);
        if (!(input instanceof HTMLInputElement)) {
            throw new Error(`Expected input ${id}`);
        }
        return input;
    };

    const renderTagsList = () => {
        const container = document.getElementById('tags-editor-list');
        if (!container) return;
        const visibleTags = tags.filter(tag => !tag.markedForDeletion);

        if (visibleTags.length === 0) {
            container.innerHTML = `<p class="no-data">${i18n.t('media:tags.noneAddBelow')}</p>`;
            return;
        }

        container.innerHTML = visibleTags.map(tag => `
            <div class="tag-editor-item" data-temp-id="${tag.tempId}">
                <div class="tag-editor-inputs">
                    <input type="text"
                           class="tag-input"
                           placeholder="${i18n.t('media:tags.name')}"
                           value="${tag.tag_name || ''}"
                           onchange="window.PHOTO_ORGANIZER.VIEW_PHOTO.photoTags.updateTagName(${tag.tempId}, this.value)">
                    <input type="text"
                           class="tag-input"
                           placeholder="${i18n.t('media:tags.valueOptional')}"
                           value="${tag.tag_value || ''}"
                           onchange="window.PHOTO_ORGANIZER.VIEW_PHOTO.photoTags.updateTagValue(${tag.tempId}, this.value)">
                </div>
                <button type="button"
                        class="btn-icon-delete"
                        data-icon="delete"
                        onclick="window.PHOTO_ORGANIZER.VIEW_PHOTO.photoTags.removeTagFromList(${tag.tempId})"
                        title="${i18n.t('media:tags.remove')}" aria-label="${i18n.t('media:tags.remove')}"></button>
            </div>
        `).join('');
    };

    const openEditModal = () => {
        tags = initialTags.map((tag, index) => ({
            ...tag,
            tempId: index,
            isNew: false
        }));
        nextTempId = tags.length;

        renderTagsList();

        getInput('modal-new-tag-name').value = '';
        getInput('modal-new-tag-value').value = '';

        modal.open();
    };

    const addTagToList = () => {
        const nameInput = document.getElementById('modal-new-tag-name');
        const valueInput = document.getElementById('modal-new-tag-value');
        if (!(nameInput instanceof HTMLInputElement) || !(valueInput instanceof HTMLInputElement)) return;

        const tagName = nameInput.value.trim();
        const tagValue = valueInput.value.trim();

        if (!tagName) {
            window.notification.error(i18n.t('media:tags.nameRequired'));
            return;
        }

        tags.push({
            tempId: nextTempId++,
            tag_name: tagName,
            tag_value: tagValue,
            isNew: true
        });

        nameInput.value = '';
        valueInput.value = '';
        nameInput.focus();

        renderTagsList();
    };

    const removeTagFromList = (/** @type {number} */ tempId) => {
        const tagIndex = tags.findIndex(t => t.tempId === tempId);
        if (tagIndex !== -1) {
            const tag = tags[tagIndex];
            if (!tag.isNew) {
                tag.markedForDeletion = true;
            } else {
                tags.splice(tagIndex, 1);
            }
        }
        renderTagsList();
    };

    const updateTagName = (/** @type {number} */ tempId, /** @type {string} */ newName) => {
        const tag = tags.find(t => t.tempId === tempId);
        if (tag) {
            tag.tag_name = newName.trim();
            tag.modified = true;
        }
    };

    const updateTagValue = (/** @type {number} */ tempId, /** @type {string} */ newValue) => {
        const tag = tags.find(t => t.tempId === tempId);
        if (tag) {
            tag.tag_value = newValue.trim();
            tag.modified = true;
        }
    };

    const saveAllChanges = async (/** @type {SubmitEvent} */ event) => {
        event.preventDefault();

        // Nothing touched -> just close (avoids a needless write + media_modified event).
        const hasChanges = tags.some(t => t.isNew || t.modified || t.markedForDeletion);
        if (!hasChanges) {
            modal.close();
            return;
        }

        // The final tag set is everything not marked for deletion; all must be named.
        const finalTags = tags.filter(t => !t.markedForDeletion);
        if (finalTags.some(t => !(t.tag_name || '').trim())) {
            window.notification.error(i18n.t('media:tags.allNeedName'));
            return;
        }

        const payload = finalTags.map(t => ({
            tag_name: (t.tag_name || '').trim(),
            tag_value: (t.tag_value || '').trim()
        }));

        try {
            const response = await fetch(config.buildUrl('update_media_tags', { media_item_id: photoId }), {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tags: payload })
            });

            if (response.ok) {
                modal.close();
                // Queue the confirmation so it survives the reload below.
                window.notification.flash(i18n.t('media:tags.updateSucceeded'));
                window.location.reload();
            } else {
                const data = await response.json().catch(() => ({}));
                window.notification.error(data.error || i18n.t('media:tags.updateFailed'));
            }
        } catch (error) {
            window.notification.error(i18n.t('media:tags.saveFailed'));
            console.error(error);
        }
    };

    if (modal.formElement) modal.formElement.addEventListener('submit', saveAllChanges);

    return {
        openEditModal,
        addTagToList,
        removeTagFromList,
        updateTagName,
        updateTagValue
    };
};
