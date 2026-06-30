import { loadModule } from './support/load_module.js';

const loadMultiSelect = async () => {
  const PO = await loadModule('multi-select.js');
  PO.COMPONENTS.multiSelect.initAll();
};

const fixture = ({
  checked = [],
  searchable = true,
  matchType = true,
  dataLabel = true,
} = {}) => {
  document.body.innerHTML = `
    ${matchType ? '<div id="people-match-type"></div>' : ''}
    <div class="multi-select-wrapper"
         data-placeholder="All People"
         data-single-format="Only {name}"
         data-multi-format="{count} people selected"
         ${matchType ? 'data-match-type-id="people-match-type"' : ''}
         ${searchable ? 'data-searchable="true" data-search-placeholder="Search people"' : ''}>
      <div class="multi-select-header">
        <span class="selected-text">All People</span>
        <span class="arrow">v</span>
      </div>
      <div class="multi-select-options">
        <label class="multi-select-option">
          <input type="checkbox" name="person" value="ada" ${dataLabel ? 'data-label="Ada"' : ''} ${checked.includes('ada') ? 'checked' : ''}>
          <span>Ada Lovelace</span>
        </label>
        <label class="multi-select-option">
          <input type="checkbox" name="person" value="grace" data-label="Grace" ${checked.includes('grace') ? 'checked' : ''}>
          <span>Grace Hopper</span>
        </label>
      </div>
    </div>`;
};

const firstCheckbox = () => document.querySelector('input[type="checkbox"]');
const selectedText = () => document.querySelector('.selected-text').textContent;

describe('multi-select globals', () => {
  it('initializes selected text and searchable inputs after i18n is ready', async () => {
    fixture({ checked: ['ada'] });

    await loadMultiSelect();

    expect(selectedText()).toBe('Only Ada');
    const search = document.querySelector('.multi-select-search');
    expect(search).not.toBeNull();
    expect(search.placeholder).toBe('Search people');
  });

  it('uses the placeholder when nothing is selected', async () => {
    fixture();

    await loadMultiSelect();

    expect(selectedText()).toBe('All People');
  });

  it('uses the default label text when data-label is absent', async () => {
    fixture({ checked: ['ada'], dataLabel: false });

    await loadMultiSelect();

    expect(selectedText()).toBe('Only Ada Lovelace');
  });

  it('formats multiple selections and reveals the match type selector', async () => {
    fixture({ checked: ['ada', 'grace'] });

    await loadMultiSelect();

    expect(selectedText()).toBe('2 people selected');
    expect(document.getElementById('people-match-type').style.display).toBe('flex');
  });

  it('hides the match type selector for fewer than two selections', async () => {
    fixture({ checked: ['ada'] });

    await loadMultiSelect();

    expect(document.getElementById('people-match-type').style.display).toBe('none');
  });

  it('toggles open state and focuses the search box', async () => {
    fixture();
    await loadMultiSelect();
    const focus = vi.spyOn(document.querySelector('.multi-select-search'), 'focus');

    window.toggleMultiSelect(document.querySelector('.multi-select-header'));

    expect(document.querySelector('.multi-select-wrapper').classList.contains('open')).toBe(true);
    expect(focus).toHaveBeenCalledTimes(1);
  });

  it('filters options case-insensitively from the injected search input', async () => {
    fixture();
    await loadMultiSelect();

    const search = document.querySelector('.multi-select-search');
    search.value = 'grace';
    search.dispatchEvent(new Event('input'));

    const [ada, grace] = Array.from(document.querySelectorAll('.multi-select-option'));
    expect(ada.classList.contains('multi-select-option--hidden')).toBe(true);
    expect(grace.classList.contains('multi-select-option--hidden')).toBe(false);
  });

  it('prevents Enter in the search input from submitting the parent form', async () => {
    fixture();
    await loadMultiSelect();

    const event = new KeyboardEvent('keydown', { key: 'Enter', cancelable: true });
    document.querySelector('.multi-select-search').dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
  });

  it('closes open dropdowns when clicking outside', async () => {
    fixture();
    await loadMultiSelect();
    document.querySelector('.multi-select-wrapper').classList.add('open');

    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(document.querySelector('.multi-select-wrapper').classList.contains('open')).toBe(false);
  });

  it('updates text when the exported update function is called after a checkbox change', async () => {
    fixture();
    await loadMultiSelect();

    const checkbox = firstCheckbox();
    checkbox.checked = true;
    window.updateMultiSelectText(checkbox);

    expect(selectedText()).toBe('Only Ada');
  });

  it('does not inject duplicate search inputs on repeated init', async () => {
    fixture();
    await loadMultiSelect();

    window.initSearchableMultiSelects();

    expect(document.querySelectorAll('.multi-select-search')).toHaveLength(1);
  });
});
