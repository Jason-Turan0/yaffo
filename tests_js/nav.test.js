import { loadModule } from './support/load_module.js';

const loadNav = async () => (await loadModule('nav.js')).COMPONENTS.initNavPagesBar;

// The shell contract (docs/development/responsive.md, "The shared panel
// contract") makes the Menu toggle, the primary destination list and the
// context-panel host required peers of the pages bar: initNavPagesBar returns
// early without them, so the fixture has to carry the whole navbar.
const fixture = (visible = false) => {
  document.body.innerHTML = `
    <nav class="navbar">
      <div class="navbar-container">
        <button id="nav-menu-toggle" aria-expanded="false"></button>
        <div id="navbar-primary"><a href="/">Home</a></div>
        <button id="nav-pages-toggle"></button>
      </div>
      <div id="navbar-context-panels" hidden></div>
      <div id="navbar-pages-bar" ${visible ? '' : 'hidden'}></div>
    </nav>`;
  const navbar = document.querySelector('.navbar');
  Object.defineProperty(navbar, 'offsetHeight', { configurable: true, value: 72 });
};

beforeEach(() => {
  localStorage.clear();
  window.APP_CONFIG.urls.pages_bar_visibility = '/pages/bar-visibility';
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 }));
  document.documentElement.style.removeProperty('--navbar-height');
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe('initNavPagesBar', () => {
  it('returns undefined when required elements are missing', async () => {
    document.body.innerHTML = '';
    const initNavPagesBar = await loadNav();

    expect(initNavPagesBar(window.testI18n, window.APP_CONFIG)).toBeUndefined();
  });

  it('initializes the collapsed default and publishes navbar height', async () => {
    fixture();
    const initNavPagesBar = await loadNav();

    const api = initNavPagesBar(window.testI18n, window.APP_CONFIG);

    expect(document.getElementById('navbar-pages-bar').hidden).toBe(true);
    expect(document.getElementById('nav-pages-toggle').getAttribute('aria-expanded')).toBe('false');
    expect(document.documentElement.style.getPropertyValue('--navbar-height')).toBe('72px');
    expect(api.syncNavbarHeight).toEqual(expect.any(Function));
  });

  it('uses server-rendered visibility and ignores the old localStorage preference', async () => {
    localStorage.setItem('yaffo.pagesBarHidden', '1');
    fixture(true);
    const initNavPagesBar = await loadNav();

    initNavPagesBar(window.testI18n, window.APP_CONFIG);

    const toggle = document.getElementById('nav-pages-toggle');
    expect(document.getElementById('navbar-pages-bar').hidden).toBe(false);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(toggle.classList.contains('collapsed')).toBe(false);
  });

  it('toggles and persists state when the button is clicked', async () => {
    fixture();
    const initNavPagesBar = await loadNav();
    initNavPagesBar(window.testI18n, window.APP_CONFIG);

    document.getElementById('nav-pages-toggle').click();

    await vi.waitFor(() => expect(document.getElementById('nav-pages-toggle').disabled).toBe(false));
    expect(document.getElementById('navbar-pages-bar').hidden).toBe(false);
    expect(fetch).toHaveBeenNthCalledWith(1, '/pages/bar-visibility', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ visible: true }),
    });

    document.getElementById('nav-pages-toggle').click();

    await vi.waitFor(() => expect(document.getElementById('nav-pages-toggle').disabled).toBe(false));
    expect(document.getElementById('navbar-pages-bar').hidden).toBe(true);
    expect(fetch).toHaveBeenNthCalledWith(2, '/pages/bar-visibility', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ visible: false }),
    });
    expect(localStorage.getItem('yaffo.pagesBarHidden')).toBeNull();
  });

  it('resyncs navbar height on resize', async () => {
    fixture();
    const navbar = document.querySelector('.navbar');
    const initNavPagesBar = await loadNav();
    initNavPagesBar(window.testI18n, window.APP_CONFIG);
    Object.defineProperty(navbar, 'offsetHeight', { configurable: true, value: 88 });

    window.dispatchEvent(new Event('resize'));

    expect(document.documentElement.style.getPropertyValue('--navbar-height')).toBe('88px');
  });

  it.each(['http', 'network'])('restores the saved visibility when saving fails (%s)', async (failure) => {
    fixture();
    const errorLog = vi.spyOn(console, 'error').mockImplementation(() => {});
    if (failure === 'http') fetch.mockResolvedValue({ ok: false, status: 500 });
    else fetch.mockRejectedValue(new Error('offline'));
    const initNavPagesBar = await loadNav();
    initNavPagesBar(window.testI18n, window.APP_CONFIG);

    const toggle = document.getElementById('nav-pages-toggle');
    toggle.click();

    await vi.waitFor(() => expect(toggle.disabled).toBe(false));
    expect(document.getElementById('navbar-pages-bar').hidden).toBe(true);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.classList.contains('collapsed')).toBe(true);
    expect(errorLog).toHaveBeenCalled();
    expect(window.notification.failure).toHaveBeenCalledWith('common.saveFailed');
    errorLog.mockRestore();
  });

});
