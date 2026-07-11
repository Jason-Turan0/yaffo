// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.filters = window.PHOTO_ORGANIZER.filters || {};
const filters = window.PHOTO_ORGANIZER.filters;

/**
 * @param {I18nService} i18n
 * @param {AppConfig} config
 * @returns {TagsFilterApi}
 */
window.PHOTO_ORGANIZER.filters.initTags = (i18n, config) => {
    /**
     * @param {string} tagName
     * @param {string | null} [selectedValue]
     */
    const loadTagValues = async (tagName, selectedValue = null) => {
        const tagValueSelect = document.getElementById('tag-value-select');
        if (!(tagValueSelect instanceof HTMLSelectElement)) return;
        const tagValueWrapper = tagValueSelect.nextElementSibling;

        if (!tagName) {
            tagValueSelect.innerHTML = `<option value="">— ${i18n.t('media:filters.selectTagNameFirst')} —</option>`;
            tagValueSelect.disabled = true;

            if (tagValueWrapper && tagValueWrapper.classList.contains('searchable-select-wrapper')) {
                tagValueWrapper.classList.add('disabled');
            }
            return;
        }

        if (selectedValue === null) {
            selectedValue = tagValueSelect.value;
        }

        tagValueSelect.disabled = true;
        if (tagValueWrapper && tagValueWrapper.classList.contains('searchable-select-wrapper')) {
            tagValueWrapper.classList.add('disabled');
        }

        tagValueSelect.innerHTML = `<option value="">${i18n.t('media:filters.loading')}</option>`;

        try {
            // URL API instead of string concatenation: the endpoint may
            // already carry query params (the remote gallery's proxy does).
            const url = new URL(config.urls.get_tag_values, window.location.origin);
            url.searchParams.set('tag_name', tagName);
            const response = await fetch(url);

            if (!response.ok) {
                throw new Error('Failed to load tag values');
            }

            const data = /** @type {{ values: string[] }} */ (await response.json());

            tagValueSelect.innerHTML = `<option value="">— ${i18n.t('media:filters.allValues')} —</option>`;

            data.values.forEach((value) => {
                const option = document.createElement('option');
                option.value = value;
                option.textContent = value;
                tagValueSelect.appendChild(option);
            });

            if (selectedValue && data.values.includes(selectedValue)) {
                tagValueSelect.value = selectedValue;
            }

            tagValueSelect.disabled = false;
            if (tagValueWrapper && tagValueWrapper.classList.contains('searchable-select-wrapper')) {
                tagValueWrapper.classList.remove('disabled');
            }
        } catch (error) {
            console.error('Error loading tag values:', error);
            tagValueSelect.innerHTML = `<option value="">— ${i18n.t('media:filters.loadValuesError')} —</option>`;
            tagValueSelect.disabled = true;
            if (tagValueWrapper && tagValueWrapper.classList.contains('searchable-select-wrapper')) {
                tagValueWrapper.classList.add('disabled');
            }
            window.notification.error(i18n.t('media:filters.loadValuesFailed'));
        }
    };

    const tagNameSelect = document.getElementById('tag-name-select');

    if (tagNameSelect instanceof HTMLSelectElement && tagNameSelect.value) {
        loadTagValues(tagNameSelect.value);
    }

    const api = { loadTagValues };
    filters.tags = api;
    return api;
};
