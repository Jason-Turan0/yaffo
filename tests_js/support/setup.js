// Shared test setup: provides the implicit browser globals that yaffo/static
// modules assume exist (they are wired up in base.html in production).
//
// `loadModule` reads these off `window` at module-evaluation time, so they must
// be in place before any module is loaded. Re-run before each test so a test that
// mutates them (e.g. asserting notification calls) starts clean.

const createTestI18n = (overrides = {}) => ({
  locale: 'en-US',
  t: (key) => key,
  number: (value) => String(value),
  percent: (value) => `${Math.round(value * 100)}%`,
  date: (value, options = {}) => new Intl.DateTimeFormat('en-US', options).format(new Date(value)),
  relativeTime: (value, unit, options = {}) =>
    new Intl.RelativeTimeFormat('en-US', options).format(value, unit),
  list: (values, options = {}) => new Intl.ListFormat('en-US', options).format(values),
  ...overrides,
});

const response = (body, { ok = true, status = 200 } = {}) => ({
  ok,
  status,
  json: vi.fn(() => Promise.resolve(body)),
});

const installTestI18next = () => {
  window.i18next = {
    init: vi.fn(() => Promise.resolve()),
    t: vi.fn((key, options = {}) => `${key}:${JSON.stringify(options)}`),
  };
  return window.i18next;
};

const stubCatalogFetch = (catalogs) => {
  const fetchMock = vi.fn((url) => {
    if (!(url in catalogs)) {
      return Promise.resolve(response({}, { ok: false, status: 404 }));
    }
    const value = catalogs[url];
    if (value instanceof Error) {
      return Promise.reject(value);
    }
    return Promise.resolve(response(value));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
};

beforeEach(() => {
  window.testI18n = createTestI18n();
  // jsdom implements no matchMedia. nav.js keys the whole narrow-shell contract
  // off one media query, so without this every module that reads it at init
  // throws and takes its test file down with it.
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    });
  }
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

  window.PHOTO_ORGANIZER = {
    COMPONENTS: {},
    i18n: window.testI18n,
    i18nReady: Promise.resolve(window.testI18n),
  };

  installTestI18next();

  window.notification = {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    show: vi.fn(),
  };

  window.testHelpers = {
    createTestI18n,
    installTestI18next,
    response,
    stubCatalogFetch,
  };
});
