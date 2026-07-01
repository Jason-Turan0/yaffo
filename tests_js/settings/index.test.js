import { loadModule } from '../support/load_module.js';

// settings.init wires the Settings page: adding/removing media directories (JSON
// fetch + confirm dialog + re-render) and a live thumbnail-stats NDJSON stream that
// fills the count/size the same way index_photos.js does. These tests drive the
// returned public API against a stubbed fetch.

const config = () => ({
  urls: {
    add_media_dir: '/settings/media-dir',
    settings_thumbnail_stats_stream: '/settings/thumbnail-stats/stream',
  },
  buildUrl: (endpoint, params = {}) => {
    let url = `/${endpoint}`;
    for (const [k, v] of Object.entries(params)) url += `/${k}/${v}`;
    return url;
  },
});

const jsonResponse = (body, { ok = true } = {}) => ({
  ok,
  json: () => Promise.resolve(body),
});

// A Response-like object whose body streams the given string chunks as UTF-8,
// matching the reader interface streamThumbnailStats consumes. Built fresh per
// call so the same fetch mock can be read more than once.
const streamResponse = (chunks, { ok = true } = {}) => {
  const encoder = new TextEncoder();
  let i = 0;
  return {
    ok,
    body: {
      getReader: () => ({
        read: () =>
          i < chunks.length
            ? Promise.resolve({ done: false, value: encoder.encode(chunks[i++]) })
            : Promise.resolve({ done: true, value: undefined }),
      }),
    },
  };
};

const stubFetch = (impl) => {
  const fetchMock = vi.fn(impl);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
};

const streamFetch = (chunks, opts) => stubFetch(() => Promise.resolve(streamResponse(chunks, opts)));

const init = async (mediaDirs = []) =>
  (await loadModule('settings/index.js')).settings.init(mediaDirs, window.testI18n, config());

afterEach(() => vi.unstubAllGlobals());

describe('settings media directories', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div class="main-content">
        <input id="new-media-dir">
        <div id="media-dirs-list"></div>
      </div>`;
    window.PHOTO_ORGANIZER.confirmDialog = vi.fn(() => Promise.resolve(true));
  });

  it('adds a directory, re-renders the list, and clears the input', async () => {
    stubFetch(() => Promise.resolve(jsonResponse({ media_dirs: [{ path: '/photos' }] })));
    document.getElementById('new-media-dir').value = '/photos';
    const api = await init();

    await api.addMediaDir();

    expect(document.querySelectorAll('.media-dir-item').length).toBe(1);
    expect(document.querySelector('.media-dir-path').textContent).toBe('/photos');
    expect(document.getElementById('new-media-dir').value).toBe('');
    expect(window.notification.success).toHaveBeenCalled();
  });

  it('warns and does not fetch when the path is empty', async () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse({})));
    const api = await init();

    await api.addMediaDir();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(window.notification.error).toHaveBeenCalled();
  });

  it('escapes the rendered directory path', async () => {
    stubFetch(() => Promise.resolve(jsonResponse({ media_dirs: [{ path: '<img src=x>' }] })));
    document.getElementById('new-media-dir').value = 'x';
    const api = await init();

    await api.addMediaDir();

    const path = document.querySelector('.media-dir-path');
    expect(path.querySelector('img')).toBeNull();
    expect(path.textContent).toBe('<img src=x>');
  });

  it('removes a directory after confirmation and shows the empty state', async () => {
    stubFetch(() => Promise.resolve(jsonResponse({ media_dirs: [], removed: '/photos' })));
    const api = await init([{ path: '/photos' }]);

    await api.removeMediaDir(0);

    expect(window.PHOTO_ORGANIZER.confirmDialog).toHaveBeenCalled();
    expect(document.querySelector('.no-data')).not.toBeNull();
    expect(window.notification.success).toHaveBeenCalled();
  });

  it('does nothing when removal is not confirmed', async () => {
    window.PHOTO_ORGANIZER.confirmDialog = vi.fn(() => Promise.resolve(false));
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse({})));
    const api = await init([{ path: '/photos' }]);

    await api.removeMediaDir(0);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('routes a data-action click through the main-content listener', async () => {
    stubFetch(() => Promise.resolve(jsonResponse({ media_dirs: [{ path: '/x' }] })));
    document.querySelector('.main-content').insertAdjacentHTML(
      'beforeend',
      '<button data-action="add-media-dir">Add</button>',
    );
    document.getElementById('new-media-dir').value = '/x';
    await init();

    document.querySelector('[data-action="add-media-dir"]').click();

    await vi.waitFor(() => expect(document.querySelectorAll('.media-dir-item').length).toBe(1));
  });
});

describe('settings thumbnail stats stream', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div id="thumbnail-stats">
        <span id="thumbnail-count"></span>
        <span id="thumbnail-size"></span>
      </div>`;
  });

  const count = () => document.getElementById('thumbnail-count').textContent;
  const size = () => document.getElementById('thumbnail-size').textContent;

  it('bumps the count on progress and fills count + size on done', async () => {
    streamFetch([
      '{"type":"progress","scanned":3}\n',
      '{"type":"done","count":10,"size":2048}\n',
    ]);
    const api = await init();

    await api.streamThumbnailStats();

    expect(count()).toBe('10');
    // formatBytes delegates to i18n.t; the test i18n echoes the key back.
    expect(size()).toBe('settings:thumbnail.size');
  });

  it('reassembles a record split across two chunks', async () => {
    streamFetch(['{"type":"progr', 'ess","scanned":42}\n']);
    const api = await init();

    await api.streamThumbnailStats();

    expect(count()).toBe('42');
  });

  it('processes a trailing record that has no terminating newline', async () => {
    streamFetch(['{"type":"done","count":7,"size":1024}']);
    const api = await init();

    await api.streamThumbnailStats();

    expect(count()).toBe('7');
  });

  it('dashes the size and notifies on an error record', async () => {
    streamFetch(['{"type":"error","message":"boom"}\n']);
    const api = await init();

    await api.streamThumbnailStats();

    expect(size()).toBe('—');
    expect(window.notification.error).toHaveBeenCalled();
  });

  it('notifies and dashes the size when the stream request fails', async () => {
    streamFetch([], { ok: false });
    const api = await init();

    await api.streamThumbnailStats();

    expect(window.notification.error).toHaveBeenCalled();
    expect(size()).toBe('—');
  });
});
