import { loadModule } from '../support/load_module.js';

// pages.initDesignGrid drives the AI page-builder design surface on top of the
// vendored gridstack (stubbed here): it reflects the generation phase in the UI,
// polls a working draft's status, reconciles widgets, and commits/publishes on
// Save. These tests stub GridStack + fetch and drive the wired controls.

const config = () => ({
  urls: {},
  buildUrl: (endpoint, params = {}) => {
    let url = `/${endpoint}`;
    for (const [k, v] of Object.entries(params)) url += `/${k}/${v}`;
    return url;
  },
});

let gridInstance;
const stubGridStack = () => {
  gridInstance = {
    engine: { nodes: [] },
    on: vi.fn(),
    setStatic: vi.fn(),
    enableMove: vi.fn(),
    enableResize: vi.fn(),
    addWidget: vi.fn(),
    removeWidget: vi.fn(),
    getRow: vi.fn(() => 0),
    update: vi.fn(),
  };
  const gs = { init: vi.fn(() => gridInstance) };
  vi.stubGlobal('GridStack', gs);
  return gs;
};

const okJson = (body) => ({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve('') });
const okText = (text) => ({ ok: true, status: 200, text: () => Promise.resolve(text), json: () => Promise.resolve({}) });

// Route fetches by a URL fragment; anything unmatched resolves to an empty 200.
const stubFetch = (routes) => {
  const mock = vi.fn((url) => {
    for (const [fragment, make] of Object.entries(routes)) {
      if (String(url).includes(fragment)) return Promise.resolve(make());
    }
    return Promise.resolve(okJson({}));
  });
  vi.stubGlobal('fetch', mock);
  return mock;
};

const designFixture = () => {
  document.body.innerHTML = `
    <div class="page-design">
      <div class="grid-stack"></div>
      <div id="conversation-messages"></div>
      <form id="conversation-form">
        <input id="conversation-message">
        <button type="submit">Send</button>
      </form>
      <button id="conversation-cancel"></button>
      <div id="conversation-status"></div>
      <div id="conversation-elapsed"></div>
      <button id="add-widget-button"></button>
      <button id="save-page-button"></button>
      <input id="page-title" value="My Page">
      <input id="page-subtitle" value="Sub">
      <input id="page-show-title" type="checkbox" checked>
      <input id="page-tab-order" value="3">
    </div>`;
  window.PHOTO_ORGANIZER.confirmDialog = vi.fn(() => Promise.resolve(true));
};

const initDesign = async (status = 'ACCEPTED', versionId = 42) =>
  (await loadModule('pages/grid.js')).pages.initDesignGrid(1, versionId, status, config(), window.testI18n);

// initDesignGrid assigns window.location.href / calls reload(); replace location
// with a plain stub so those don't trip jsdom's unimplemented navigation.
const originalLocation = window.location;
beforeEach(() => {
  Object.defineProperty(window, 'location', {
    configurable: true,
    writable: true,
    value: { href: 'http://localhost/', assign: vi.fn(), reload: vi.fn() },
  });
});
afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, writable: true, value: originalLocation });
  vi.unstubAllGlobals();
});

describe('pages initPresentationGrid', () => {
  it('builds a static grid', async () => {
    const gs = stubGridStack();
    const result = (await loadModule('pages/grid.js')).pages.initPresentationGrid();
    expect(gs.init).toHaveBeenCalledWith(expect.objectContaining({ staticGrid: true }));
    expect(result).toBe(gridInstance);
  });
});

describe('pages initDesignGrid — idle (published) version', () => {
  beforeEach(designFixture);

  it('returns the edit version id and does not start polling', async () => {
    const gs = stubGridStack();
    const fetchMock = stubFetch({});
    const api = await initDesign('ACCEPTED', 42);

    expect(gs.init).toHaveBeenCalled();
    expect(api.getVersionId()).toBe(42);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows the idle button state', async () => {
    stubGridStack();
    stubFetch({});
    await initDesign('ACCEPTED', 42);

    expect(document.getElementById('save-page-button').disabled).toBe(false);
    expect(document.getElementById('add-widget-button').disabled).toBe(false);
    expect(document.getElementById('conversation-cancel').disabled).toBe(true);
    expect(document.getElementById('conversation-status').hidden).toBe(true);
    expect(gridInstance.setStatic).toHaveBeenCalledWith(false);
  });

  it('Save commits the page payload', async () => {
    stubGridStack();
    const fetchMock = stubFetch({ pages_update: () => okJson({}) });
    await initDesign('ACCEPTED', 42);

    document.getElementById('save-page-button').click();

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain('/pages_update/page_id/1');
    expect(JSON.parse(opts.body)).toEqual({
      title: 'My Page',
      subtitle: 'Sub',
      show_title: true,
      tab_order: 3,
      widgets: [],
    });
    expect(window.location.href).toContain('/pages_detail/page_id/1');
  });

  it('Add widget renders a draft shell onto the grid', async () => {
    stubGridStack();
    const fetchMock = stubFetch({
      pages_widget_preview: () =>
        okText('<div class="grid-stack-item"><div class="grid-stack-item-content"></div></div>'),
    });
    await initDesign('ACCEPTED', 42);

    document.getElementById('add-widget-button').click();

    await vi.waitFor(() => expect(gridInstance.addWidget).toHaveBeenCalled());
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('pages_widget_preview'))).toBe(true);
  });
});

describe('pages initDesignGrid — working draft under review', () => {
  beforeEach(designFixture);

  it('polls a READY draft, renders the feed, and publishes on Save', async () => {
    stubGridStack();
    const fetchMock = stubFetch({
      pages_version_status: () =>
        okJson({
          status: 'READY',
          started_at: null,
          messages: [{ type: 'assistant', content: 'done' }],
          widgets: [],
        }),
      pages_version_publish: () => okJson({}),
    });

    await initDesign('READY', 7);

    // The status poll drives the conversation feed.
    await vi.waitFor(() =>
      expect(document.querySelector('#conversation-messages .chat-message')).not.toBeNull());
    expect(document.querySelector('.chat-message-assistant').textContent).toBe('done');

    // A READY draft publishes (not plain-saves) on Save.
    document.getElementById('save-page-button').click();
    await vi.waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes('pages_version_publish'))).toBe(true));
  });
});
