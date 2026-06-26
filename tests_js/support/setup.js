// Shared test setup: provides the implicit browser globals that yaffo/static
// modules assume exist (they are wired up in base.html in production).
//
// `loadModule` reads these off `window` at module-evaluation time, so they must
// be in place before any module is loaded. Re-run before each test so a test that
// mutates them (e.g. asserting notification calls) starts clean.

beforeEach(() => {
  window.APP_CONFIG = {
    i18n: {
      locale: 'en-US',
      fallbackLocale: 'en',
      resourceUrl: '/locales/__lng__.json',
    },
    urls: {},
    buildUrl: (endpoint, params = {}) => {
      let url = `/${endpoint}`;
      for (const [k, v] of Object.entries(params)) url += `/${k}/${v}`;
      return url;
    },
  };

  window.notification = {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    show: vi.fn(),
  };
});
