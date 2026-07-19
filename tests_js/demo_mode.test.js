import { loadModule } from './support/load_module.js';

const mockResponse = (body, { status = 200, contentType = 'application/json' } = {}) => ({
  status,
  headers: { get: (name) => (name === 'Content-Type' ? contentType : null) },
  clone() {
    return this;
  },
  json: () => Promise.resolve(body),
});

const loadDemoMode = async ({ demoMode = true } = {}) => {
  window.APP_CONFIG.demoMode = demoMode;
  await loadModule('demo-mode.js');
};

afterEach(() => vi.unstubAllGlobals());

describe('demo-mode.js', () => {
  it('does nothing outside demo mode', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(mockResponse({}, { status: 429 })));
    vi.stubGlobal('fetch', fetchMock);
    await loadDemoMode({ demoMode: false });

    await window.fetch('/x');

    expect(window.notification.warning).not.toHaveBeenCalled();
    expect(window.notification.info).not.toHaveBeenCalled();
  });

  it('shows a warning toast on a 429 rate-limit response', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        mockResponse(
          { error: 'The public demo is busy. Please wait a moment and try again.', code: 'demo_rate_limit_exceeded' },
          { status: 429 },
        ),
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    await loadDemoMode();

    await window.fetch('/sharing/devices/x/transfers/pull');

    expect(window.notification.warning).toHaveBeenCalledWith(
      'The public demo is busy. Please wait a moment and try again.',
      5000,
    );
    expect(window.notification.info).not.toHaveBeenCalled();
  });

  it('shows an info toast on a 403 feature-disabled response', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        mockResponse(
          { error: 'This action is disabled in the public demo.', code: 'demo_feature_disabled' },
          { status: 403 },
        ),
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    await loadDemoMode();

    await window.fetch('/albums/create', { method: 'POST' });

    expect(window.notification.info).toHaveBeenCalledWith('This action is disabled in the public demo.', 5000);
    expect(window.notification.warning).not.toHaveBeenCalled();
  });

  it('ignores unrelated error statuses', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(mockResponse({}, { status: 500 })));
    vi.stubGlobal('fetch', fetchMock);
    await loadDemoMode();

    await window.fetch('/x');

    expect(window.notification.warning).not.toHaveBeenCalled();
    expect(window.notification.info).not.toHaveBeenCalled();
  });

  it('suppresses the HTMX swap and shows a warning toast on a 429 response', async () => {
    await loadDemoMode();

    const xhr = {
      status: 429,
      getResponseHeader: () => 'application/json',
      responseText: JSON.stringify({ error: 'Slow down', code: 'demo_rate_limit_exceeded' }),
    };
    const detail = { xhr, shouldSwap: true };
    document.dispatchEvent(new CustomEvent('htmx:beforeSwap', { detail }));

    expect(detail.shouldSwap).toBe(false);
    expect(window.notification.warning).toHaveBeenCalledWith('Slow down', 5000);
  });
});
