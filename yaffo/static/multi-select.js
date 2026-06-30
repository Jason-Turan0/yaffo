// @ts-check

/**
 * @typedef {Object} I18nService
 * @property {(key: string, options?: Record<string, unknown>) => string} t
 */

const multiSelectWindow = /** @type {Window & {
    PHOTO_ORGANIZER: {
        i18n: I18nService,
        i18nReady: Promise<I18nService>,
    },
    toggleMultiSelect: (header: Element) => void,
    updateMultiSelectText: (checkbox: HTMLInputElement) => void,
    filterMultiSelectOptions: (input: HTMLInputElement) => void,
    initSearchableMultiSelects: () => void,
}} */ (/** @type {unknown} */ (window));

/**
 * @param {Element} header
 */
const toggleMultiSelect = (header) => {
    const wrapper = header.parentElement;
    if (!wrapper) return;
    wrapper.classList.toggle('open');
    // Drop focus into the search box when opening a searchable dropdown.
    if (wrapper.classList.contains('open')) {
        const search = /** @type {HTMLInputElement | null} */ (
            wrapper.querySelector('.multi-select-search')
        );
        if (search) search.focus();
    }
};

/**
 * @param {HTMLInputElement} checkbox
 */
const updateMultiSelectText = (checkbox) => {
    const i18n = multiSelectWindow.PHOTO_ORGANIZER.i18n;
    const wrapper = /** @type {HTMLElement | null} */ (
        checkbox.closest('.multi-select-wrapper')
    );
    if (!wrapper) return;
    const header = wrapper.querySelector('.selected-text');
    if (!header) return;
    const checkboxes = wrapper.querySelectorAll('input[type="checkbox"]:checked');

    // Get data attributes using dataset
    const placeholder = wrapper.dataset.placeholder || i18n.t('common:all');
    const singleFormat = wrapper.dataset.singleFormat || '{name}';
    const multiFormat = wrapper.dataset.multiFormat || i18n.t('components:multiSelect.selected', {
        count: '{count}',
    });

    if (checkboxes.length === 0) {
        header.textContent = placeholder;
    } else if (checkboxes.length === 1) {
        const selected = /** @type {HTMLInputElement} */ (checkboxes[0]);
        const defaultLabel = selected.nextElementSibling?.textContent || '';
        // Get the label from the checkbox's data attribute
        const label = selected.dataset.label || defaultLabel;
        header.textContent = singleFormat.replace('{name}', label);
    } else {
        header.textContent = multiFormat.replace('{count}', String(checkboxes.length));
    }
     // Show/hide match type selector
    const matchTypeId = wrapper.dataset.matchTypeId;
    if (matchTypeId) {
        const matchTypeElement = document.getElementById(matchTypeId);
        if (matchTypeElement) {
            if (checkboxes.length >= 2) {
                matchTypeElement.style.display = 'flex';
            } else {
                matchTypeElement.style.display = 'none';
            }
        }
    }
};

// Hide options whose label text doesn't contain the search term (case-insensitive).
/**
 * @param {HTMLInputElement} input
 */
const filterMultiSelectOptions = (input) => {
    const wrapper = /** @type {HTMLElement | null} */ (
        input.closest('.multi-select-wrapper')
    );
    if (!wrapper) return;
    const term = input.value.trim().toLowerCase();
    wrapper.querySelectorAll('.multi-select-option').forEach(option => {
        const text = (option.textContent || '').trim().toLowerCase();
        option.classList.toggle('multi-select-option--hidden', term !== '' && !text.includes(term));
    });
};

// Inject a search box into any wrapper that opted in with data-searchable="true".
const initSearchableMultiSelects = () => {
    document.querySelectorAll('.multi-select-wrapper[data-searchable="true"]').forEach(wrapper => {
        const multiSelect = /** @type {HTMLElement} */ (wrapper);
        const options = wrapper.querySelector('.multi-select-options');
        if (!options || options.querySelector('.multi-select-search')) return;

        const searchWrapper = document.createElement('div');
        searchWrapper.className = 'multi-select-search-wrapper';

        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'multi-select-search';
        input.placeholder = multiSelect.dataset.searchPlaceholder
            || multiSelectWindow.PHOTO_ORGANIZER.i18n.t('common:search');
        input.addEventListener('input', () => filterMultiSelectOptions(input));
        // The box lives inside the filter form; Enter would submit it, so swallow it.
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') e.preventDefault();
        });

        searchWrapper.append(input);
        options.prepend(searchWrapper);
    });
};

multiSelectWindow.toggleMultiSelect = toggleMultiSelect;
multiSelectWindow.updateMultiSelectText = updateMultiSelectText;
multiSelectWindow.filterMultiSelectOptions = filterMultiSelectOptions;
multiSelectWindow.initSearchableMultiSelects = initSearchableMultiSelects;

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
    if (!(e.target instanceof Element) || !e.target.closest('.multi-select-wrapper')) {
        document.querySelectorAll('.multi-select-wrapper.open').forEach(wrapper => {
            wrapper.classList.remove('open');
        });
    }
});

// Initialize text and search boxes on page load
multiSelectWindow.PHOTO_ORGANIZER.i18nReady.then(() => {
    initSearchableMultiSelects();
    document.querySelectorAll('.multi-select-wrapper').forEach(wrapper => {
        const firstCheckbox = /** @type {HTMLInputElement | null} */ (
            wrapper.querySelector('input[type="checkbox"]')
        );
        if (firstCheckbox) {
            updateMultiSelectText(firstCheckbox);
        }
    });
});
