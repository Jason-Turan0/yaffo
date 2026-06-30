import { loadModule } from './support/load_module.js';

const loadSearchableSelect = async () => {
  await loadModule('searchable-select.js');
  await Promise.resolve();
  return window.SearchableSelect;
};

const fixture = ({ disabled = false, searchDisabled = false } = {}) => {
  document.documentElement.dataset.selectSearch = 'Type to search';
  document.documentElement.dataset.selectNoResults = 'No results';
  document.documentElement.dataset.selectPlaceholder = 'Select';
  document.body.innerHTML = `
    <label>
      Person
      <select class="searchable-select"${disabled ? ' disabled' : ''}${searchDisabled ? ' data-search-disabled' : ''}>
        <option value="">Choose one</option>
        <option value="ada">Ada Lovelace</option>
        <option value="grace" selected>Grace Hopper</option>
        <option value="katherine">Katherine Johnson</option>
      </select>
    </label>`;
  Element.prototype.scrollIntoView = vi.fn();
  return document.querySelector('select');
};

const optionTexts = () =>
  Array.from(document.querySelectorAll('.searchable-select-option')).map((el) => el.textContent);

describe('SearchableSelect', () => {
  it('auto-initializes searchable selects and hides the original select', async () => {
    const select = fixture();

    await loadSearchableSelect();

    expect(select.dataset.searchableInitialized).toBe('true');
    expect(select.style.display).toBe('none');
    expect(document.querySelector('.searchable-select-text').textContent).toBe('Grace Hopper');
    expect(optionTexts()).toEqual(['Choose one', 'Ada Lovelace', 'Grace Hopper', 'Katherine Johnson']);
  });

  it('filters options from the search input and shows the no-results state', async () => {
    fixture();
    await loadSearchableSelect();

    const search = document.querySelector('.searchable-select-search');
    search.value = 'ada';
    search.dispatchEvent(new Event('input', { bubbles: true }));

    expect(optionTexts()).toEqual(['Ada Lovelace']);

    search.value = 'zzz';
    search.dispatchEvent(new Event('input', { bubbles: true }));

    expect(document.querySelector('.searchable-select-no-results').textContent).toBe('components:select.noResults');
  });

  it('renders the search input on open and filters visible options while typing', async () => {
    fixture();
    await loadSearchableSelect();

    document.querySelector('.searchable-select-display').click();

    expect(document.querySelector('.searchable-select-wrapper').classList.contains('open')).toBe(true);
    const search = document.querySelector('.searchable-select-search');
    expect(search).not.toBeNull();
    expect(search.placeholder).toBe('components:select.typeToSearch');

    search.value = 'hop';
    search.dispatchEvent(new Event('input', { bubbles: true }));

    expect(optionTexts()).toEqual(['Grace Hopper']);

    search.value = '';
    search.dispatchEvent(new Event('input', { bubbles: true }));

    expect(optionTexts()).toEqual(['Choose one', 'Ada Lovelace', 'Grace Hopper', 'Katherine Johnson']);
  });

  it('selects an option, dispatches change, updates text, and closes', async () => {
    const select = fixture();
    const change = vi.fn();
    select.addEventListener('change', change);
    await loadSearchableSelect();

    document.querySelector('[data-value="ada"]').click();

    expect(select.value).toBe('ada');
    expect(change).toHaveBeenCalledTimes(1);
    expect(document.querySelector('.searchable-select-text').textContent).toBe('Ada Lovelace');
    expect(document.querySelector('.searchable-select-wrapper').classList.contains('open')).toBe(false);
  });

  it('supports keyboard navigation and enter selection', async () => {
    const select = fixture();
    await loadSearchableSelect();
    const display = document.querySelector('.searchable-select-display');
    const wrapper = document.querySelector('.searchable-select-wrapper');

    display.click();
    wrapper.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    wrapper.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    wrapper.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));

    expect(select.value).toBe('ada');
  });

  it('omits the search input when search is disabled but still supports keyboard selection', async () => {
    const select = fixture({ searchDisabled: true });
    await loadSearchableSelect();

    expect(document.querySelector('.searchable-select-search')).toBeNull();
    const display = document.querySelector('.searchable-select-display');
    const wrapper = document.querySelector('.searchable-select-wrapper');

    display.click();
    expect(wrapper.classList.contains('open')).toBe(true);
    wrapper.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    wrapper.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));

    expect(select.value).toBe('');
  });

  it('does not open disabled selects', async () => {
    fixture({ disabled: true });
    await loadSearchableSelect();

    document.querySelector('.searchable-select-display').click();

    expect(document.querySelector('.searchable-select-wrapper').classList.contains('open')).toBe(false);
    expect(document.querySelector('.searchable-select-wrapper').classList.contains('disabled')).toBe(true);
  });

  it('initializes selects added by htmx swaps and skips already-initialized selects', async () => {
    const select = fixture();
    const SearchableSelect = await loadSearchableSelect();

    SearchableSelect.init(select);
    expect(document.querySelectorAll('.searchable-select-wrapper')).toHaveLength(1);

    document.body.insertAdjacentHTML('beforeend', `
      <select class="searchable-select">
        <option value="one">One</option>
      </select>`);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    await Promise.resolve();

    expect(document.querySelectorAll('.searchable-select-wrapper')).toHaveLength(2);
  });
});
