function toggleMultiSelect(header) {
    const wrapper = header.parentElement;
    wrapper.classList.toggle('open');
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

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
    if (!e.target.closest('.multi-select-wrapper')) {
        document.querySelectorAll('.multi-select-wrapper.open').forEach(wrapper => {
            wrapper.classList.remove('open');
        });
    }
});

// Initialize text on page load
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.multi-select-wrapper').forEach(wrapper => {
        const firstCheckbox = wrapper.querySelector('input[type="checkbox"]');
        if (firstCheckbox) {
            updateMultiSelectText(firstCheckbox);
        }
    });
});