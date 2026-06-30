import { loadModule } from './support/load_module.js';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('PHOTO_ORGANIZER.initI18n', () => {
  it('loads locale and fallback catalogs through i18next', async () => {
    const i18next = window.testHelpers.installTestI18next();
    window.APP_CONFIG.i18n = {
      locale: 'fr',
      fallbackLocale: 'en',
      resourceUrl: '/locales/__lng__.json',
    };
    const fetchMock = window.testHelpers.stubCatalogFetch({
      '/locales/fr.json': { common: { hello: 'Bonjour' } },
      '/locales/en.json': { common: { hello: 'Hello' }, components: {} },
    });

    const ready = vi.fn();
    document.addEventListener('yaffo:i18n-ready', ready, { once: true });

    const PO = await loadModule('i18n.js');
    const service = await PO.i18nReady;

    expect(fetchMock).toHaveBeenCalledWith('/locales/fr.json');
    expect(fetchMock).toHaveBeenCalledWith('/locales/en.json');
    expect(i18next.init).toHaveBeenCalledWith({
      lng: 'fr',
      fallbackLng: 'en',
      resources: {
        fr: { common: { hello: 'Bonjour' } },
        en: { common: { hello: 'Hello' }, components: {} },
      },
      ns: ['common', 'components'],
      defaultNS: 'common',
      interpolation: { escapeValue: false },
    });
    expect(PO.i18n).toBe(service);
    expect(service.locale).toBe('fr');
    expect(ready).toHaveBeenCalledTimes(1);
  });

  it('falls back to an empty locale catalog when the locale fetch fails', async () => {
    const i18next = window.testHelpers.installTestI18next();
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    window.APP_CONFIG.i18n = {
      locale: 'es',
      fallbackLocale: 'en',
      resourceUrl: '/locales/__lng__.json',
    };
    window.testHelpers.stubCatalogFetch({
      '/locales/es.json': new Error('network down'),
      '/locales/en.json': { common: { hello: 'Hello' } },
    });

    const PO = await loadModule('i18n.js');
    await PO.i18nReady;

    expect(consoleError).toHaveBeenCalledTimes(1);
    expect(i18next.init.mock.calls[0][0].resources).toEqual({
      es: {},
      en: { common: { hello: 'Hello' } },
    });

    consoleError.mockRestore();
  });

  it('reuses the locale catalog when locale and fallback are the same', async () => {
    const i18next = window.testHelpers.installTestI18next();
    window.APP_CONFIG.i18n = {
      locale: 'en',
      fallbackLocale: 'en',
      resourceUrl: '/locales/__lng__.json',
    };
    const fetchMock = window.testHelpers.stubCatalogFetch({
      '/locales/en.json': { common: { hello: 'Hello' } },
    });

    const PO = await loadModule('i18n.js');
    await PO.i18nReady;

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(i18next.init.mock.calls[0][0].resources).toEqual({
      en: { common: { hello: 'Hello' } },
    });
  });

  it('returns a formatter service backed by i18next and Intl', async () => {
    const i18next = window.testHelpers.installTestI18next();
    window.APP_CONFIG.i18n = {
      locale: 'en-US',
      fallbackLocale: 'en-US',
      resourceUrl: '/locales/__lng__.json',
    };
    window.testHelpers.stubCatalogFetch({
      '/locales/en-US.json': { common: {} },
    });

    const PO = await loadModule('i18n.js');
    const service = await PO.i18nReady;

    expect(service.t('common:greeting', { name: 'Jason' })).toBe('common:greeting:{"name":"Jason"}');
    expect(i18next.t).toHaveBeenCalledWith('common:greeting', { name: 'Jason' });
    expect(service.number(1234.5)).toBe('1,234.5');
    expect(service.percent(0.25)).toBe('25%');
    expect(service.relativeTime(-1, 'day')).toBe('1 day ago');
    expect(service.list(['A', 'B'])).toBe('A and B');
    expect(service.date('2024-01-02T00:00:00Z', { timeZone: 'UTC' })).toMatch(/1\/2\/2024/);
  });
});
