/**
 * Searchable Select Component
 * Converts standard <select> elements into searchable dropdowns with filtering
 *
 * Usage:
 * 1. Add 'searchable-select' class to any <select> element
 * 2. Component will automatically initialize on DOMContentLoaded
 * 3. Dynamically call SearchableSelect.init(selectElement) for runtime-created selects
 */

class SearchableSelect {
    constructor(selectElement) {
        this.select = selectElement;
        this.wrapper = null;
        this.searchInput = null;
        this.optionsList = null;
        this.options = [];
        this.isOpen = false;
        this.highlightedIndex = -1;

        this.init();
    }

    init() {
        // Store original options
        this.updateOptions();

        // Create wrapper structure
        this.createWrapper();

        // Bind events
        this.bindEvents();

        // Mark as initialized
        this.select.dataset.searchableInitialized = 'true';
    }

    updateOptions() {
        this.options = Array.from(this.select.options).map(option => ({
            value: option.value,
            text: option.textContent,
            selected: option.selected
        }));
    }

    createWrapper() {
        // Create wrapper
        this.wrapper = document.createElement('div');
        this.wrapper.className = 'searchable-select-wrapper';

        // Create display button
        const displayButton = document.createElement('button');
        displayButton.type = 'button';
        displayButton.className = 'searchable-select-display';
        displayButton.innerHTML = `
            <span class="searchable-select-text">${this.getSelectedText()}</span>
            <span class="searchable-select-arrow">▼</span>
        `;

        // Create dropdown container
        const dropdown = document.createElement('div');
        dropdown.className = 'searchable-select-dropdown';

        // Create search input
        this.searchInput = document.createElement('input');
        this.searchInput.type = 'text';
        this.searchInput.className = 'searchable-select-search';
        this.searchInput.placeholder = 'Type to search...';

        // Create options list
        this.optionsList = document.createElement('div');
        this.optionsList.className = 'searchable-select-options';

        // Build options
        this.renderOptions();

        // Assemble structure
        dropdown.appendChild(this.searchInput);
        dropdown.appendChild(this.optionsList);
        this.wrapper.appendChild(displayButton);
        this.wrapper.appendChild(dropdown);

        // Insert after original select and hide select
        this.select.style.display = 'none';
        this.select.parentNode.insertBefore(this.wrapper, this.select.nextSibling);

        // Store references
        this.displayButton = displayButton;
        this.displayText = displayButton.querySelector('.searchable-select-text');
        this.dropdown = dropdown;
    }

    renderOptions(filter = '') {
        this.optionsList.innerHTML = '';
        this.highlightedIndex = -1;

        const filteredOptions = this.options.filter(opt =>
            opt.text.toLowerCase().includes(filter.toLowerCase())
        );

        if (filteredOptions.length === 0) {
            const noResults = document.createElement('div');
            noResults.className = 'searchable-select-no-results';
            noResults.textContent = 'No results found';
            this.optionsList.appendChild(noResults);
            return;
        }

        filteredOptions.forEach((option, index) => {
            const optionDiv = document.createElement('div');
            optionDiv.className = 'searchable-select-option';
            optionDiv.textContent = option.text;
            optionDiv.dataset.value = option.value;
            optionDiv.dataset.index = index;

            if (option.selected) {
                optionDiv.classList.add('selected');
            }

            optionDiv.addEventListener('click', () => this.selectOption(option.value));

            this.optionsList.appendChild(optionDiv);
        });
    }

    bindEvents() {
        // Toggle dropdown
        this.displayButton.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggle();
        });

        // Search input
        this.searchInput.addEventListener('input', (e) => {
            this.renderOptions(e.target.value);
        });

        // Keyboard navigation
        this.searchInput.addEventListener('keydown', (e) => {
            const optionElements = this.optionsList.querySelectorAll('.searchable-select-option');
            const optionCount = optionElements.length;

            if (optionCount === 0) return;

            switch(e.key) {
                case 'ArrowDown':
                    e.preventDefault();
                    this.highlightedIndex = Math.min(this.highlightedIndex + 1, optionCount - 1);
                    this.updateHighlight();
                    break;

                case 'ArrowUp':
                    e.preventDefault();
                    this.highlightedIndex = Math.max(this.highlightedIndex - 1, 0);
                    this.updateHighlight();
                    break;

                case 'Enter':
                    e.preventDefault();
                    if (this.highlightedIndex >= 0 && this.highlightedIndex < optionCount) {
                        const highlightedOption = optionElements[this.highlightedIndex];
                        this.selectOption(highlightedOption.dataset.value);
                    }
                    break;

                case 'Escape':
                    e.preventDefault();
                    this.close();
                    break;
            }
        });

        // Prevent dropdown close when clicking search input
        this.searchInput.addEventListener('click', (e) => {
            e.stopPropagation();
        });

        // Close on outside click
        document.addEventListener('click', (e) => {
            if (!this.wrapper.contains(e.target)) {
                this.close();
            }
        });

        // Listen for changes to the underlying select (for dynamic updates)
        const observer = new MutationObserver(() => {
            this.updateOptions();
            this.renderOptions(this.searchInput.value);
            this.updateDisplayText();
        });

        observer.observe(this.select, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['selected']
        });
    }

    toggle() {
        if (this.isOpen) {
            this.close();
        } else {
            this.open();
        }
    }

    open() {
        this.isOpen = true;
        this.wrapper.classList.add('open');
        this.searchInput.value = '';
        this.renderOptions('');
        this.searchInput.focus();
    }

    close() {
        this.isOpen = false;
        this.wrapper.classList.remove('open');
        this.highlightedIndex = -1;
    }

    updateHighlight() {
        const optionElements = this.optionsList.querySelectorAll('.searchable-select-option');

        optionElements.forEach((el, index) => {
            if (index === this.highlightedIndex) {
                el.classList.add('highlighted');
                // Scroll into view if needed
                el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            } else {
                el.classList.remove('highlighted');
            }
        });
    }

    selectOption(value) {
        // Update the underlying select
        this.select.value = value;

        // Trigger change event
        const event = new Event('change', { bubbles: true });
        this.select.dispatchEvent(event);

        // Update display
        this.updateDisplayText();

        // Update selected state in options
        this.updateOptions();

        // Close dropdown
        this.close();
    }

    updateDisplayText() {
        this.displayText.textContent = this.getSelectedText();
    }

    getSelectedText() {
        const selectedOption = this.select.options[this.select.selectedIndex];
        return selectedOption ? selectedOption.textContent : 'Select...';
    }

    // Static method to initialize all searchable selects on the page
    static initAll() {
        document.querySelectorAll('select.searchable-select:not([data-searchable-initialized])').forEach(select => {
            new SearchableSelect(select);
        });
    }

    // Static method to initialize a specific select
    static init(selectElement) {
        if (selectElement.dataset.searchableInitialized) {
            return;
        }
        new SearchableSelect(selectElement);
    }
}

// Auto-initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    SearchableSelect.initAll();
});

// Export for use in other scripts
window.SearchableSelect = SearchableSelect;