import { loadModule } from '../support/load_module.js';

const loadDateInput = async () => (await loadModule('components/intl_date_input.js')).COMPONENTS.intlDateInput;

const fixture = (value = '2024-03-15') => {
  document.body.innerHTML = `
    <form>
      <div class="intl-date-input-control">
        <input class="intl-date-input" type="text">
        <input type="hidden" name="taken_at" value="${value}">
      </div>
    </form>`;
  return document.querySelector('.intl-date-input-control');
};

describe('intlDateInput pure helpers', () => {
  it('formats ISO values in the requested locale', async () => {
    const api = await loadDateInput();

    expect(api.formatValue('2024-03-15', 'en-US')).toBe('03/15/2024');
    expect(api.formatValue('2024-03-15', 'en-GB')).toBe('15/03/2024');
    expect(api.formatValue('', 'en-US')).toBe('');
  });

  it('builds a locale-shaped placeholder', async () => {
    const api = await loadDateInput();

    expect(api.placeholder('en-US')).toBe('MM/DD/YYYY');
    expect(api.placeholder('en-GB')).toBe('DD/MM/YYYY');
  });

  it('parses ISO, compact ISO, and locale-ordered values', async () => {
    const api = await loadDateInput();

    expect(api.parseDate('2024-03-15', 'en-US')).toBe('2024-03-15');
    expect(api.parseDate('20240315', 'en-US')).toBe('2024-03-15');
    expect(api.parseDate('03/15/2024', 'en-US')).toBe('2024-03-15');
    expect(api.parseDate('15/03/2024', 'en-GB')).toBe('2024-03-15');
  });

  it('rejects invalid and incomplete dates', async () => {
    const api = await loadDateInput();

    expect(api.parseDate('', 'en-US')).toBe('');
    expect(api.parseDate('02/30/2024', 'en-US')).toBeNull();
    expect(api.parseDate('tomorrow', 'en-US')).toBeNull();
  });

  it('normalizes localized digits', async () => {
    const api = await loadDateInput();

    expect(api.parseDate('١٥/٠٣/٢٠٢٤', 'ar-EG')).toBe('2024-03-15');
  });

  it('formats partial typed values using locale order', async () => {
    const api = await loadDateInput();

    expect(api.formatPartial('03152024', 'en-US')).toBe('03/15/2024');
    expect(api.formatPartial('15032024', 'en-GB')).toBe('15/03/2024');
  });
});

describe('intlDateInput DOM initializer', () => {
  it('initializes visible value and exposes a control object', async () => {
    const api = await loadDateInput();
    const root = fixture();

    const control = api.init(root, window.testI18n);

    expect(root.querySelector('.intl-date-input').placeholder).toBe('MM/DD/YYYY');
    expect(root.querySelector('.intl-date-input').value).toBe('03/15/2024');
    expect(root.intlDateInput).toBe(control);
  });

  it('syncs a valid visible value back to the hidden field on blur', async () => {
    const api = await loadDateInput();
    const root = fixture('');
    api.init(root, window.testI18n);

    const visible = root.querySelector('.intl-date-input');
    const hidden = root.querySelector('input[type="hidden"]');
    visible.value = '04/20/2024';
    visible.dispatchEvent(new Event('blur'));

    expect(hidden.value).toBe('2024-04-20');
    expect(visible.classList.contains('is-invalid')).toBe(false);
  });

  it('marks invalid input and blocks form submit', async () => {
    const api = await loadDateInput();
    const root = fixture('');
    api.init(root, window.testI18n);

    const visible = root.querySelector('.intl-date-input');
    const form = document.querySelector('form');
    const reportValidity = vi.spyOn(visible, 'reportValidity').mockImplementation(() => true);

    visible.value = '02/30/2024';
    const submit = new Event('submit', { cancelable: true });
    form.dispatchEvent(submit);

    expect(submit.defaultPrevented).toBe(true);
    expect(visible.classList.contains('is-invalid')).toBe(true);
    expect(visible.validationMessage).toBe('components:dateInput.invalidDate');
    expect(reportValidity).toHaveBeenCalledTimes(1);
  });

  it('initializes every control in a scope', async () => {
    const api = await loadDateInput();
    document.body.innerHTML = `
      <section>
        <div class="intl-date-input-control">
          <input class="intl-date-input" type="text">
          <input type="hidden" value="2024-01-01">
        </div>
        <div class="intl-date-input-control">
          <input class="intl-date-input" type="text">
          <input type="hidden" value="2024-01-02">
        </div>
      </section>`;

    const controls = api.initAll(window.testI18n, document.querySelector('section'));

    expect(controls).toHaveLength(2);
    expect(document.querySelectorAll('.intl-date-input')[1].value).toBe('01/02/2024');
  });
});
