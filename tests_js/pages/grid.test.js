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

// ---------------------------------------------------------------------------
// Canvas policy (responsive). The bands are measured on the .grid-stack element,
// so these drive it by stubbing that element's clientWidth and letting the
// module's observer run its initial pass.
// ---------------------------------------------------------------------------

// A gridstack stub with enough engine behaviour to observe the policy: column()
// flattens x/w the way the real one does, and update() merges onto the node.
const stubResponsiveGridStack = () => {
  const nodes = [];
  gridInstance = {
    engine: { nodes, swap: vi.fn(() => true) },
    opts: { column: 12 },
    on: vi.fn(),
    setStatic: vi.fn(),
    enableMove: vi.fn(),
    enableResize: vi.fn(),
    addWidget: vi.fn(),
    removeWidget: vi.fn(),
    getRow: vi.fn(() => 0),
    getColumn: vi.fn(() => gridInstance.opts.column),
    batchUpdate: vi.fn(),
    compact: vi.fn(),
    column: vi.fn((count) => {
      gridInstance.opts.column = count;
      if (count === 1) nodes.forEach((node) => { node.x = 0; node.w = 1; });
    }),
    update: vi.fn((el, opts) => {
      const node = nodes.find((candidate) => candidate.el === el);
      if (node) Object.assign(node, opts);
    }),
  };
  vi.stubGlobal('GridStack', { init: vi.fn(() => gridInstance) });
  return gridInstance;
};

// Two widgets side by side in the authored 12-column layout: a short wide one
// (the case a one-column reflow squeezes) and a tall one.
const responsiveFixture = (canvasWidth) => {
  designFixture();
  const grid = document.querySelector('.grid-stack');
  grid.innerHTML = `
    <div class="grid-stack-item" gs-id="a" gs-x="0" gs-y="0" gs-w="6" gs-h="2">
      <div class="grid-stack-item-content widget-card">
        <div class="widget-header">
          <input type="text" class="widget-title-input" value="Stats" hidden>
          <button class="widget-order widget-order-up"></button>
          <button class="widget-order widget-order-down"></button>
          <button class="widget-order widget-size-shorter"></button>
          <button class="widget-order widget-size-taller"></button>
        </div>
      </div>
    </div>
    <div class="grid-stack-item" gs-id="b" gs-x="6" gs-y="0" gs-w="6" gs-h="5">
      <div class="grid-stack-item-content widget-card">
        <div class="widget-header">
          <input type="text" class="widget-title-input" value="Gallery" hidden>
        </div>
      </div>
    </div>`;
  Object.defineProperty(grid, 'clientWidth', { configurable: true, value: canvasWidth });
  return grid;
};

// Seed the stub engine from the fixture markup, as gridstack would on init.
const seedNodes = (instance) => {
  document.querySelectorAll('.grid-stack > .grid-stack-item').forEach((el) => {
    const node = {
      id: el.getAttribute('gs-id'),
      el,
      x: Number(el.getAttribute('gs-x')),
      y: Number(el.getAttribute('gs-y')),
      w: Number(el.getAttribute('gs-w')),
      h: Number(el.getAttribute('gs-h')),
    };
    el.gridstackNode = node;
    instance.engine.nodes.push(node);
  });
};

describe('pages initDesignGrid — canvas bands', () => {
  it('drops a narrow canvas to one column and raises widgets to its height floor', async () => {
    const instance = stubResponsiveGridStack();
    responsiveFixture(390);
    seedNodes(instance);
    stubFetch({});

    await initDesign('ACCEPTED', 42);

    expect(instance.column).toHaveBeenCalledWith(1);
    expect(document.querySelector('.grid-stack').classList.contains('is-single-column')).toBe(true);
    // The 2-row widget is raised to the one-column floor; the 5-row one is left alone.
    expect(instance.engine.nodes.find((n) => n.id === 'a').h).toBe(3);
    expect(instance.engine.nodes.find((n) => n.id === 'b').h).toBe(5);
  });

  it('uses six columns on an intermediate canvas', async () => {
    const instance = stubResponsiveGridStack();
    responsiveFixture(700);
    seedNodes(instance);
    stubFetch({});

    await initDesign('ACCEPTED', 42);

    expect(instance.column).toHaveBeenCalledWith(6);
    expect(document.querySelector('.grid-stack').classList.contains('is-single-column')).toBe(false);
  });

  it('turns drag gestures off on a single-column canvas and on for a wide one', async () => {
    const narrow = stubResponsiveGridStack();
    responsiveFixture(390);
    seedNodes(narrow);
    stubFetch({});
    // matchMedia stubs every query to matches:false, i.e. a coarse pointer.
    await initDesign('ACCEPTED', 42);
    expect(narrow.enableMove).toHaveBeenCalledWith(false);
    expect(document.querySelector('.grid-stack').classList.contains('is-direct-controls')).toBe(true);
  });

  it('Save writes the authored 12-column layout even while the canvas is narrowed', async () => {
    const instance = stubResponsiveGridStack();
    responsiveFixture(390);
    seedNodes(instance);
    const fetchMock = stubFetch({ pages_update: () => okJson({}) });

    await initDesign('ACCEPTED', 42);
    // The live grid really is flattened...
    expect(instance.engine.nodes.every((node) => node.w === 1)).toBe(true);

    document.getElementById('save-page-button').click();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());

    // ...but Save publishes the authored geometry, not the reflow.
    const { widgets } = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(widgets).toEqual([
      { id: 'a', x: 0, y: 0, w: 6, h: 2, title: 'Stats' },
      { id: 'b', x: 6, y: 0, w: 6, h: 5, title: 'Gallery' },
    ]);
  });

  it('Move down still reorders when gridstack refuses the swap', async () => {
    const instance = stubResponsiveGridStack();
    // gridstack's engine.swap() only reorders items that are the same size or
    // touching. On an authoring canvas (float: true) a shrink or a delete leaves
    // a gap between two neighbours, and swap() then gives up — which used to make
    // Move down do nothing at all on the one path a phone has for reordering.
    instance.engine.swap = vi.fn(() => false);
    responsiveFixture(390);
    seedNodes(instance);
    stubFetch({});

    await initDesign('ACCEPTED', 42);
    const [first, second] = instance.engine.nodes;
    second.y = 4;  // the hole a shrink left behind

    document.querySelector('[gs-id="a"] .widget-order-down').click();

    expect([first.y, second.y]).toEqual([4, 0]);
    // One column is a stack, so the exchange is followed by closing the gaps.
    expect(instance.compact).toHaveBeenCalled();
  });

  it('the taller control moves the authored height, not only the reflowed one', async () => {
    const instance = stubResponsiveGridStack();
    responsiveFixture(390);
    seedNodes(instance);
    const fetchMock = stubFetch({ pages_update: () => okJson({}) });

    await initDesign('ACCEPTED', 42);
    document.querySelector('[gs-id="a"] .widget-size-taller').click();

    document.getElementById('save-page-button').click();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const { widgets } = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(widgets[0].h).toBe(3);
  });
});
