function toggleMultiSelect(header) {
    const wrapper = header.parentElement;
    wrapper.classList.toggle('open');
    // Drop focus into the search box when opening a searchable dropdown.
    if (wrapper.classList.contains('open')) {
        const search = wrapper.querySelector('.multi-select-search');
        if (search) search.focus();
    }
}

function updateMultiSelectText(checkbox) {
    const wrapper = checkbox.closest('.multi-select-wrapper');
    const header = wrapper.querySelector('.selected-text');
    const checkboxes = wrapper.querySelectorAll('input[type="checkbox"]:checked');

    // Get data attributes using dataset
    const placeholder = wrapper.dataset.placeholder || 'All';
    const singleFormat = wrapper.dataset.singleFormat || '{name}';
    const multiFormat = wrapper.dataset.multiFormat || '{count} selected';

    if (checkboxes.length === 0) {
        header.textContent = placeholder;
    } else if (checkboxes.length === 1) {
        const defaultLabel = checkboxes[0].nextElementSibling.textContent;
        // Get the label from the checkbox's data attribute
        const label = checkboxes[0].dataset.label || defaultLabel;
        header.textContent = singleFormat.replace('{name}', label);
    } else {
        header.textContent = multiFormat.replace('{count}', checkboxes.length);
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
}

// Hide options whose label text doesn't contain the search term (case-insensitive).
function filterMultiSelectOptions(input) {
    const wrapper = input.closest('.multi-select-wrapper');
    const term = input.value.trim().toLowerCase();
    wrapper.querySelectorAll('.multi-select-option').forEach(option => {
        const text = option.textContent.trim().toLowerCase();
        option.classList.toggle('multi-select-option--hidden', term !== '' && !text.includes(term));
    });
}

// Inject a search box into any wrapper that opted in with data-searchable="true".
function initSearchableMultiSelects() {
    document.querySelectorAll('.multi-select-wrapper[data-searchable="true"]').forEach(wrapper => {
        const options = wrapper.querySelector('.multi-select-options');
        if (!options || options.querySelector('.multi-select-search')) return;

        const searchWrapper = document.createElement('div');
        searchWrapper.className = 'multi-select-search-wrapper';

        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'multi-select-search';
        input.placeholder = wrapper.dataset.searchPlaceholder || 'Search…';
        input.addEventListener('input', () => filterMultiSelectOptions(input));
        // The box lives inside the filter form; Enter would submit it, so swallow it.
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') e.preventDefault();
        });

        searchWrapper.append(input);
        options.prepend(searchWrapper);
    });
}

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
    if (!e.target.closest('.multi-select-wrapper')) {
        document.querySelectorAll('.multi-select-wrapper.open').forEach(wrapper => {
            wrapper.classList.remove('open');
        });
    }
});

// Initialize text and search boxes on page load
document.addEventListener('DOMContentLoaded', () => {
    initSearchableMultiSelects();
    document.querySelectorAll('.multi-select-wrapper').forEach(wrapper => {
        const firstCheckbox = wrapper.querySelector('input[type="checkbox"]');
        if (firstCheckbox) {
            updateMultiSelectText(firstCheckbox);
        }
    });
});
