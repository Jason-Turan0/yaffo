window.addTag = async function() {
    const tagName = document.getElementById('new-tag-name').value.trim();
    const tagValue = document.getElementById('new-tag-value').value.trim();

    if (!tagName) {
        window.notification.error('Tag name is required');
        return;
    }

    try {
        const response = await fetch(`/api/photo/${window.PHOTO_ID}/tags`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                tag_name: tagName,
                tag_value: tagValue
            })
        });

        const data = await response.json();

        if (response.ok) {
            window.notification.success('Tag added successfully');
            window.location.reload();
        } else {
            window.notification.error(data.error || 'Failed to add tag');
        }
    } catch (error) {
        window.notification.error('Error adding tag');
        console.error(error);
    }
};

window.editTag = function(tagId, currentName, currentValue) {
    const tagItem = document.querySelector(`[data-tag-id="${tagId}"]`);
    if (!tagItem) return;

    const tagDisplay = tagItem.querySelector('.tag-display');
    tagDisplay.innerHTML = `
        <input type="text" id="edit-tag-name-${tagId}" value="${currentName}" class="tag-input-inline">
        <input type="text" id="edit-tag-value-${tagId}" value="${currentValue}" class="tag-input-inline" placeholder="Value (optional)">
    `;

    const tagActions = tagItem.querySelector('.tag-actions');
    tagActions.innerHTML = `
        <button type="button" class="tag-action-btn" onclick="saveTag(${tagId})" title="Save">
            ✅
        </button>
        <button type="button" class="tag-action-btn" onclick="window.location.reload()" title="Cancel">
            ❌
        </button>
    `;
};

window.saveTag = async function(tagId) {
    const tagName = document.getElementById(`edit-tag-name-${tagId}`).value.trim();
    const tagValue = document.getElementById(`edit-tag-value-${tagId}`).value.trim();

    if (!tagName) {
        window.notification.error('Tag name is required');
        return;
    }

    try {
        const response = await fetch(`/api/photo/tags/${tagId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                tag_name: tagName,
                tag_value: tagValue
            })
        });

        const data = await response.json();

        if (response.ok) {
            window.notification.success('Tag updated successfully');
            window.location.reload();
        } else {
            window.notification.error(data.error || 'Failed to update tag');
        }
    } catch (error) {
        window.notification.error('Error updating tag');
        console.error(error);
    }
};

window.deleteTag = async function(tagId) {
    if (!confirm('Are you sure you want to delete this tag?')) {
        return;
    }

    try {
        const response = await fetch(`/api/photo/tags/${tagId}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            window.notification.success('Tag deleted successfully');
            window.location.reload();
        } else {
            const data = await response.json();
            window.notification.error(data.error || 'Failed to delete tag');
        }
    } catch (error) {
        window.notification.error('Error deleting tag');
        console.error(error);
    }
};