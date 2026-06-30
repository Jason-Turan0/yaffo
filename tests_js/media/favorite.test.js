import { loadModule } from '../support/load_module.js';

const loadFavorite = async () => (await loadModule('media/favorite.js')).media.favorite.init;

const config = {
  buildUrl: (endpoint, params = {}) => `/${endpoint}/${params.media_item_id}`,
};

const fixture = () => {
  document.body.innerHTML = `
    <button type="button" class="favorite-toggle" data-photo-id="42" aria-pressed="false"></button>
    <button type="button" class="favorite-toggle is-favorite" data-photo-id="99" aria-pressed="true"></button>
    <button type="button" class="not-favorite" data-photo-id="100"></button>`;
};

const jsonResponse = ({ ok = true, body = {} } = {}) => ({
  ok,
  json: vi.fn(() => Promise.resolve(body)),
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('initFavoriteToggles', () => {
  it('posts the toggle request and updates favorite state', async () => {
    fixture();
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({ body: { favorite: true } })));
    vi.stubGlobal('fetch', fetchMock);
    const initFavoriteToggles = await loadFavorite();
    const button = document.querySelector('[data-photo-id="42"]');

    initFavoriteToggles(window.testI18n, config);
    button.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(fetchMock).toHaveBeenCalledWith('/toggle_media_favorite/42', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    expect(button.classList.contains('is-favorite')).toBe(true);
    expect(button.getAttribute('aria-pressed')).toBe('true');
    expect(button.disabled).toBe(false);
  });

  it('can clear favorite state', async () => {
    fixture();
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ body: { favorite: false } }))));
    const initFavoriteToggles = await loadFavorite();
    const button = document.querySelector('[data-photo-id="99"]');

    initFavoriteToggles(window.testI18n, config);
    button.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(button.classList.contains('is-favorite')).toBe(false);
    expect(button.getAttribute('aria-pressed')).toBe('false');
  });

  it('stops click propagation so card click handlers do not also fire', async () => {
    fixture();
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ body: { favorite: true } }))));
    const cardClick = vi.fn();
    document.body.addEventListener('click', cardClick);
    const initFavoriteToggles = await loadFavorite();

    initFavoriteToggles(window.testI18n, config);
    document.querySelector('[data-photo-id="42"]').click();

    expect(cardClick).not.toHaveBeenCalled();
  });

  it('shows an error notification and re-enables the button on failure', async () => {
    fixture();
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ ok: false }))));
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const initFavoriteToggles = await loadFavorite();
    const button = document.querySelector('[data-photo-id="42"]');

    initFavoriteToggles(window.testI18n, config);
    button.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(window.notification.error).toHaveBeenCalledWith('media:favorite.updateFailed');
    expect(button.disabled).toBe(false);
    consoleError.mockRestore();
  });

  it('ignores elements that are not favorite toggles', async () => {
    fixture();
    const initFavoriteToggles = await loadFavorite();
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({ body: { favorite: true } })));
    vi.stubGlobal('fetch', fetchMock);

    initFavoriteToggles(window.testI18n, config);
    document.querySelector('.not-favorite').click();

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
