async function loadTagValues(tagName) {
    const tagValueSelect = document.getElementById('tag-value-select');

    if (!tagName) {
        tagValueSelect.innerHTML = '<option value="">-- Select a tag name first --</option>';
        tagValueSelect.disabled = true;
        return;
    }

    tagValueSelect.disabled = true;
    tagValueSelect.innerHTML = '<option value="">Loading...</option>';

    try {
        const response = await fetch(`/api/tag-values?tag_name=${encodeURIComponent(tagName)}`);

        if (!response.ok) {
            throw new Error('Failed to load tag values');
        }

        const data = await response.json();

        tagValueSelect.innerHTML = '<option value="">-- All values --</option>';

        data.values.forEach(value => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = value;
            tagValueSelect.appendChild(option);
        });

        tagValueSelect.disabled = false;
    } catch (error) {
        console.error('Error loading tag values:', error);
        tagValueSelect.innerHTML = '<option value="">-- Error loading values --</option>';
        window.notification.error('Failed to load tag values');
    }
}

document.addEventListener('DOMContentLoaded', function() {
    const tagNameSelect = document.getElementById('tag-name-select');

    if (tagNameSelect && tagNameSelect.value) {
        loadTagValues(tagNameSelect.value);
    }
});