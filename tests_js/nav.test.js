import { loadModule } from './support/load_module.js';

const loadNav = async () => (await loadModule('nav.js')).COMPONENTS.initNavPagesBar;

// The shell contract (docs/development/responsive.md, "The shared panel
// contract") makes the Menu toggle, the primary destination list and the
// context-panel host required peers of the pages bar: initNavPagesBar returns
// early without them, so the fixture has to carry the whole navbar.
const fixture = () => {
  document.body.innerHTML = `
    <nav class="navbar">
      <div class="navbar-container">
        <button id="nav-menu-toggle" aria-expanded="false"></button>
        <div id="navbar-primary"><a href="/">Home</a></div>
        <button id="nav-pages-toggle"></button>
      </div>
      <div id="navbar-context-panels" hidden></div>
      <div id="navbar-pages-bar"></div>
    </nav>`;
  const navbar = document.querySelector('.navbar');
  Object.defineProperty(navbar, 'offsetHeight', { configurable: true, value: 72 });
};

beforeEach(() => {
  localStorage.clear();
  document.documentElement.style.removeProperty('--navbar-height');
});

afterEach(() => {
  localStorage.clear();
});

describe('initNavPagesBar', () => {
  it('returns undefined when required elements are missing', async () => {
    document.body.innerHTML = '';
    const initNavPagesBar = await loadNav();

    expect(initNavPagesBar()).toBeUndefined();
  });

  it('initializes visible state and publishes navbar height', async () => {
    fixture();
    const initNavPagesBar = await loadNav();

    const api = initNavPagesBar();

    expect(document.getElementById('navbar-pages-bar').hidden).toBe(false);
    expect(document.getElementById('nav-pages-toggle').getAttribute('aria-expanded')).toBe('true');
    expect(document.documentElement.style.getPropertyValue('--navbar-height')).toBe('72px');
    expect(api.syncNavbarHeight).toEqual(expect.any(Function));
  });

  it('restores the hidden state from localStorage', async () => {
    localStorage.setItem('yaffo.pagesBarHidden', '1');
    fixture();
    const initNavPagesBar = await loadNav();

    initNavPagesBar();

    const toggle = document.getElementById('nav-pages-toggle');
    expect(document.getElementById('navbar-pages-bar').hidden).toBe(true);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.classList.contains('collapsed')).toBe(true);
  });

  it('toggles and persists state when the button is clicked', async () => {
    fixture();
    const initNavPagesBar = await loadNav();
    initNavPagesBar();

    document.getElementById('nav-pages-toggle').click();

    expect(document.getElementById('navbar-pages-bar').hidden).toBe(true);
    expect(localStorage.getItem('yaffo.pagesBarHidden')).toBe('1');

    document.getElementById('nav-pages-toggle').click();

    expect(document.getElementById('navbar-pages-bar').hidden).toBe(false);
    expect(localStorage.getItem('yaffo.pagesBarHidden')).toBe('0');
  });

  it('resyncs navbar height on resize', async () => {
    fixture();
    const navbar = document.querySelector('.navbar');
    const initNavPagesBar = await loadNav();
    initNavPagesBar();
    Object.defineProperty(navbar, 'offsetHeight', { configurable: true, value: 88 });

    window.dispatchEvent(new Event('resize'));

    expect(document.documentElement.style.getPropertyValue('--navbar-height')).toBe('88px');
  });

});
