// @ts-check

/**
 * Searchable Select Component
 * Converts standard <select> elements into searchable dropdowns with filtering
 *
 * Usage:
 * 1. Add 'searchable-select' class to any <select> element
 * 2. Component will automatically initialize on DOMContentLoaded
 * 3. Dynamically call SearchableSelect.init(selectElement) for runtime-created selects
 *
 * Options (attributes on the <select>):
 * - data-search-disabled: omit the search box (for short option lists);
 *   keyboard navigation still works.
 */

/**
 * @typedef {Object} SearchableOption
 * @property {string} value
 * @property {string} text
 * @property {boolean} selected
 */

class SearchableSelect {
    /**
     * @param {HTMLSelectElement} selectElement
     */
    constructor(selectElement) {
        this.select = selectElement;
        this.searchEnabled = !selectElement.hasAttribute('data-search-disabled');
        /** @type {HTMLDivElement | null} */
        this.wrapper = null;
        /** @type {HTMLInputElement | null} */
        this.searchInput = null;
        /** @type {HTMLDivElement | null} */
        this.optionsList = null;
        /** @type {SearchableOption[]} */
        this.options = [];
        this.isOpen = false;
        this.highlightedIndex = -1;
        /** @type {HTMLButtonElement | null} */
        this.displayButton = null;
        /** @type {HTMLElement | null} */
        this.displayText = null;
        /** @type {HTMLDivElement | null} */
        this.dropdown = null;

        this.init();
    }

    /** @returns {void} */
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

    /** @returns {void} */
    updateOptions() {
        this.options = Array.from(this.select.options).map(option => ({
            value: option.value,
            text: option.textContent || '',
            selected: option.selected
        }));
    }

    /** @returns {void} */
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
        this.searchInput.placeholder = SearchableSelect.i18n.t('components:select.typeToSearch');

        // Create options list
        this.optionsList = document.createElement('div');
        this.optionsList.className = 'searchable-select-options thin-scrollbar';

        // Build options
        this.renderOptions();

        // Assemble structure
        if (this.searchEnabled) {
            dropdown.appendChild(this.searchInput);
        }
        dropdown.appendChild(this.optionsList);
        this.wrapper.appendChild(displayButton);
        this.wrapper.appendChild(dropdown);

        this.wrapper.classList.toggle('disabled', this.select.disabled);

        // Insert after original select and hide select
        this.select.style.display = 'none';
        (/** @type {Node} */ (this.select.parentNode)).insertBefore(this.wrapper, this.select.nextSibling);

        // Store references
        this.displayButton = displayButton;
        this.displayText = displayButton.querySelector('.searchable-select-text');
        this.dropdown = dropdown;
    }

    /**
     * @param {string} filter
     * @returns {void}
     */
    renderOptions(filter = '') {
        /** @type {HTMLDivElement} */ (this.optionsList).innerHTML = '';
        this.highlightedIndex = -1;

        const filteredOptions = this.options.filter(opt =>
            opt.text.toLowerCase().includes(filter.toLowerCase())
        );

        if (filteredOptions.length === 0) {
            const noResults = document.createElement('div');
            noResults.className = 'searchable-select-no-results';
            noResults.textContent = SearchableSelect.i18n.t('components:select.noResults');
            /** @type {HTMLDivElement} */ (this.optionsList).appendChild(noResults);
            return;
        }

        filteredOptions.forEach((option, index) => {
            const optionDiv = document.createElement('div');
            optionDiv.className = 'searchable-select-option';
            optionDiv.textContent = option.text;
            optionDiv.dataset.value = option.value;
            optionDiv.dataset.index = /** @type {string} */ (/** @type {unknown} */ (index));

            if (option.selected) {
                optionDiv.classList.add('selected');
            }

            optionDiv.addEventListener('click', () => this.selectOption(option.value));

            /** @type {HTMLDivElement} */ (this.optionsList).appendChild(optionDiv);
        });
    }

    /** @returns {void} */
    bindEvents() {
        // Toggle dropdown
        /** @type {HTMLButtonElement} */ (this.displayButton).addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggle();
        });

        // Search input
        /** @type {HTMLInputElement} */ (this.searchInput).addEventListener('input', (e) => {
            this.renderOptions(/** @type {HTMLInputElement} */ (e.target).value);
        });

        // Keyboard navigation (bound to the wrapper so it also works when the
        // search box is disabled and focus stays on the display button)
        /** @type {HTMLDivElement} */ (this.wrapper).addEventListener('keydown', (e) => {
            if (!this.isOpen) return;

            const optionElements = /** @type {HTMLDivElement} */ (this.optionsList)
                .querySelectorAll('.searchable-select-option');
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
                        const highlightedOption = /** @type {HTMLElement} */ (
                            optionElements[this.highlightedIndex]
                        );
                        this.selectOption(/** @type {string} */ (highlightedOption.dataset.value));
                    }
                    break;

                case 'Escape':
                    e.preventDefault();
                    this.close();
                    break;
            }
        });

        // Prevent dropdown close when clicking search input
        /** @type {HTMLInputElement} */ (this.searchInput).addEventListener('click', (e) => {
            e.stopPropagation();
        });

        this.select.addEventListener('change', () => {
            this.updateOptions();
            this.updateDisplayText();
            this.renderOptions(/** @type {HTMLInputElement} */ (this.searchInput).value);
        });

        // Close on outside click
        document.addEventListener('click', (e) => {
            if (!/** @type {HTMLDivElement} */ (this.wrapper).contains(/** @type {Node} */ (e.target))) {
                this.close();
            }
        });

        // Listen for changes to the underlying select (for dynamic updates)
        const observer = new MutationObserver(() => {
            this.updateOptions();
            this.renderOptions(/** @type {HTMLInputElement} */ (this.searchInput).value);
            this.updateDisplayText();
            /** @type {HTMLDivElement} */ (this.wrapper).classList.toggle('disabled', this.select.disabled);
        });

        observer.observe(this.select, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['selected', 'disabled']
        });
    }

    /** @returns {void} */
    toggle() {
        if (this.select.disabled) {
            return;
        }
        if (this.isOpen) {
            this.close();
        } else {
            this.open();
        }
    }

    /** @returns {void} */
    open() {
        this.isOpen = true;
        /** @type {HTMLDivElement} */ (this.wrapper).classList.add('open');
        /** @type {HTMLInputElement} */ (this.searchInput).value = '';
        this.renderOptions('');
        if (this.searchEnabled) {
            /** @type {HTMLInputElement} */ (this.searchInput).focus();
        }
    }

    /** @returns {void} */
    close() {
        this.isOpen = false;
        /** @type {HTMLDivElement} */ (this.wrapper).classList.remove('open');
        this.highlightedIndex = -1;
    }

    /** @returns {void} */
    updateHighlight() {
        const optionElements = /** @type {HTMLDivElement} */ (this.optionsList)
            .querySelectorAll('.searchable-select-option');

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

    /**
     * @param {string} value
     * @returns {void}
     */
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

    /** @returns {void} */
    updateDisplayText() {
        /** @type {HTMLElement} */ (this.displayText).textContent = this.getSelectedText();
    }

    /** @returns {string} */
    getSelectedText() {
        const selectedOption = this.select.options[this.select.selectedIndex];
        return selectedOption
            ? selectedOption.textContent || ''
            : SearchableSelect.i18n.t('components:select.select');
    }

    // Static method to initialize all searchable selects on the page
    /** @returns {void} */
    static initAll() {
        document.querySelectorAll('select.searchable-select:not([data-searchable-initialized])').forEach(select => {
            new SearchableSelect(/** @type {HTMLSelectElement} */ (select));
        });
    }

    // Static method to initialize a specific select
    /**
     * @param {HTMLSelectElement} selectElement
     * @returns {void}
     */
    static init(selectElement) {
        if (selectElement.dataset.searchableInitialized) {
            return;
        }
        new SearchableSelect(selectElement);
    }
}

/** @type {Pick<I18nService, 't'>} */
SearchableSelect.i18n = {
    /**
     * @param {string} key
     * @returns {string}
     */
    t: (key) => ({
        'components:select.typeToSearch': document.documentElement.dataset.selectSearch,
        'components:select.noResults': document.documentElement.dataset.selectNoResults,
        'components:select.select': document.documentElement.dataset.selectPlaceholder,
    })[key] || key,
};

// Auto-initialize on page load
if (window.PHOTO_ORGANIZER?.i18nReady) {
    window.PHOTO_ORGANIZER.i18nReady.then((i18n) => {
        SearchableSelect.i18n = i18n;
        SearchableSelect.initAll();
    });
} else {
    SearchableSelect.initAll();
}

// Re-initialize selects arriving in htmx-swapped fragments (initAll skips
// anything already initialized via data-searchable-initialized)
document.addEventListener('htmx:afterSwap', () => {
    if (SearchableSelect.i18n) SearchableSelect.initAll();
});

// Export for use in other scripts
window.SearchableSelect = SearchableSelect;
