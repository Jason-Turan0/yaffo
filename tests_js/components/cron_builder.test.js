import { loadModule } from '../support/load_module.js';

const loadCronBuilderComponent = async () => {
  await loadModule('components/cron_builder.js');
  return window.PHOTO_ORGANIZER.COMPONENTS;
};

describe('cronBuilder', () => {
  it('exposes a synchronous factory without waiting for global i18nReady', async () => {
    window.PHOTO_ORGANIZER.i18nReady = { then: vi.fn() };

    const { createCronBuilder, initCronBuilder } = await loadCronBuilderComponent();

    expect(createCronBuilder).toEqual(expect.any(Function));
    expect(initCronBuilder).toEqual(expect.any(Function));
    expect(window.PHOTO_ORGANIZER.i18nReady.then).not.toHaveBeenCalled();
    expect(window.PHOTO_ORGANIZER.COMPONENTS.cronBuilderReady).toBeUndefined();
  });

  it('initializes cron builders and cron descriptions for an injected document', async () => {
    document.body.innerHTML = `
      <div data-cron-builder data-cron-name="schedule"></div>
      <span data-cron="0 9 * * 1"></span>`;
    const { createCronBuilder } = await loadCronBuilderComponent();
    const cronBuilder = createCronBuilder({ i18n: window.testI18n, document });

    cronBuilder.initAll(document);

    const mount = document.querySelector('[data-cron-builder]');
    expect(mount.dataset.cronReady).toBe('1');
    expect(document.querySelector('.cron-value').name).toBe('schedule');
    expect(document.querySelector('.cron-value').value).toBe('0 * * * *');
    expect(document.querySelector('[data-cron]').textContent).toBe('components:cron.weeklyOnAt');
  });

  it('initializes, stores, and wires the shared cron builder on the components namespace', async () => {
    document.body.innerHTML = '<div data-cron-builder data-cron-name="schedule"></div>';
    const components = await loadCronBuilderComponent();

    const cronBuilder = components.initCronBuilder({ i18n: window.testI18n, document });

    expect(components.cronBuilder).toBe(cronBuilder);
    expect(cronBuilder.initAll).toEqual(expect.any(Function));
    expect(document.querySelector('[data-cron-builder]').dataset.cronReady).toBe('1');

    document.body.insertAdjacentHTML('beforeend', '<div id="swap"><span data-cron="0 9 * * 1"></span></div>');
    document.getElementById('swap').dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true }));

    expect(document.querySelector('#swap [data-cron]').textContent).toBe('components:cron.weeklyOnAt');
  });
});
