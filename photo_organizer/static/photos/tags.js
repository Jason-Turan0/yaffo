window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.VIEW_PHOTO = window.PHOTO_ORGANIZER.VIEW_PHOTO || {};
window.PHOTO_ORGANIZER.VIEW_PHOTO.initPhotoTags = (photoId, initialTags, config) => {
    const modal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('tagsModal');

    let tags = [];
    let nextTempId = -1;

    const renderTagsList = () => {
        const container = document.getElementById('tags-editor-list');
        const visibleTags = tags.filter(tag => !tag.markedForDeletion);

        if (visibleTags.length === 0) {
            container.innerHTML = '<p class="no-data">No tags. Add one below.</p>';
            return;
        }

        container.innerHTML = visibleTags.map(tag => `
            <div class="tag-editor-item" data-temp-id="${tag.tempId}">
                <div class="tag-editor-inputs">
                    <input type="text"
                           class="tag-input"
                           placeholder="Tag name"
                           value="${tag.tag_name || ''}"
                           onchange="window.PHOTO_ORGANIZER.photoTags.updateTagName(${tag.tempId}, this.value)">
                    <input type="text"
                           class="tag-input"
                           placeholder="Tag value (optional)"
                           value="${tag.tag_value || ''}"
                           onchange="window.PHOTO_ORGANIZER.photoTags.updateTagValue(${tag.tempId}, this.value)">
                </div>
                <button type="button"
                        class="btn-icon-delete"
                        onclick="window.PHOTO_ORGANIZER.photoTags.removeTagFromList(${tag.tempId})"
                        title="Remove">
                    🗑️
                </button>
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
            notification.error('Tag name is required');
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

        const operations = [];

        for (const tag of tags) {
            if (tag.markedForDeletion && !tag.isNew) {
                operations.push({
                    type: 'delete',
                    promise: fetch(`/api/photo/tags/${tag.id}`, { method: 'DELETE' })
                });
            } else if (tag.isNew) {
                if (!tag.tag_name) {
                    notification.error('All tags must have a name');
                    return;
                }
                operations.push({
                    type: 'create',
                    promise: fetch(`/api/photo/${photoId}/tags`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            tag_name: tag.tag_name,
                            tag_value: tag.tag_value
                        })
                    })
                });
            } else if (tag.modified) {
                if (!tag.tag_name) {
                    notification.error('All tags must have a name');
                    return;
                }
                operations.push({
                    type: 'update',
                    promise: fetch(`/api/photo/tags/${tag.id}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            tag_name: tag.tag_name,
                            tag_value: tag.tag_value
                        })
                    })
                });
            }
        }

        if (operations.length === 0) {
            modal.close();
            return;
        }

        try {
            const results = await Promise.all(operations.map(op => op.promise));

            const allSuccessful = results.every(r => r.ok);

            if (allSuccessful) {
                notification.success('Tags updated successfully');
                modal.close();
                window.location.reload();
            } else {
                notification.error('Some tag operations failed');
            }
        } catch (error) {
            notification.error('Error saving tags');
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