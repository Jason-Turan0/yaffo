async function loadTagValues(tagName, selectedValue = null) {
    const tagValueSelect = document.getElementById('tag-value-select');
    const tagValueWrapper = tagValueSelect.nextElementSibling;

    if (!tagName) {
        tagValueSelect.innerHTML = '<option value="">-- Select a tag name first --</option>';
        tagValueSelect.disabled = true;

        // Update wrapper disabled state
        if (tagValueWrapper && tagValueWrapper.classList.contains('searchable-select-wrapper')) {
            tagValueWrapper.classList.add('disabled');
        }
        return;
    }

    // Store the current selected value if not provided
    if (selectedValue === null) {
        selectedValue = tagValueSelect.value;
    }

    tagValueSelect.disabled = true;
    if (tagValueWrapper && tagValueWrapper.classList.contains('searchable-select-wrapper')) {
        tagValueWrapper.classList.add('disabled');
    }

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

        // Restore the selected value if it exists in the loaded values
        if (selectedValue && data.values.includes(selectedValue)) {
            tagValueSelect.value = selectedValue;
        }

        tagValueSelect.disabled = false;
        if (tagValueWrapper && tagValueWrapper.classList.contains('searchable-select-wrapper')) {
            tagValueWrapper.classList.remove('disabled');
        }
    } catch (error) {
        console.error('Error loading tag values:', error);
        tagValueSelect.innerHTML = '<option value="">-- Error loading values --</option>';
        tagValueSelect.disabled = true;
        if (tagValueWrapper && tagValueWrapper.classList.contains('searchable-select-wrapper')) {
            tagValueWrapper.classList.add('disabled');
        }
        window.notification.error('Failed to load tag values');
    }
}

document.addEventListener('DOMContentLoaded', function() {
    const tagNameSelect = document.getElementById('tag-name-select');

    if (tagNameSelect && tagNameSelect.value) {
        loadTagValues(tagNameSelect.value);
    }
});