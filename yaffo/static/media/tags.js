window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.VIEW_PHOTO = window.PHOTO_ORGANIZER.VIEW_PHOTO || {};
window.PHOTO_ORGANIZER.VIEW_PHOTO.initPhotoTags = (photoId, initialTags, i18n, config) => {
    const modal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('tagsModal');

    let tags = [];
    let nextTempId = -1;

    const renderTagsList = () => {
        const container = document.getElementById('tags-editor-list');
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

        document.getElementById('modal-new-tag-name').value = '';
        document.getElementById('modal-new-tag-value').value = '';

        modal.open();
    };

    const addTagToList = () => {
        const nameInput = document.getElementById('modal-new-tag-name');
        const valueInput = document.getElementById('modal-new-tag-value');

        const tagName = nameInput.value.trim();
        const tagValue = valueInput.value.trim();

        if (!tagName) {
            notification.error(i18n.t('media:tags.nameRequired'));
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

    const removeTagFromList = (tempId) => {
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

    const updateTagName = (tempId, newName) => {
        const tag = tags.find(t => t.tempId === tempId);
        if (tag) {
            tag.tag_name = newName.trim();
            tag.modified = true;
        }
    };

    const updateTagValue = (tempId, newValue) => {
        const tag = tags.find(t => t.tempId === tempId);
        if (tag) {
            tag.tag_value = newValue.trim();
            tag.modified = true;
        }
    };

    const saveAllChanges = async (event) => {
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
            notification.error(i18n.t('media:tags.allNeedName'));
            return;
        }

        const payload = finalTags.map(t => ({
            tag_name: t.tag_name.trim(),
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
                notification.flash(i18n.t('media:tags.updateSucceeded'));
                window.location.reload();
            } else {
                const data = await response.json().catch(() => ({}));
                notification.error(data.error || i18n.t('media:tags.updateFailed'));
            }
        } catch (error) {
            notification.error(i18n.t('media:tags.saveFailed'));
            console.error(error);
        }
    };

    modal.formElement.addEventListener('submit', saveAllChanges);

    return {
        openEditModal,
        addTagToList,
        removeTagFromList,
        updateTagName,
        updateTagValue
    };
};
