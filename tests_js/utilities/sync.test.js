import { loadModule } from '../support/load_module.js';

const i18n = { t: (key, options = {}) => {
  if (options.count !== undefined) return `${key}:${options.count}`;
  if (options.shown !== undefined) return `${key}:${options.shown}/${options.total}`;
  return key;
} };

const tiles = (out) => [...out.querySelectorAll('.sync-test-stat')].map((tile) =>
  [tile.querySelector('dd').textContent, tile.querySelector('dt').textContent]);

describe('sync Test results', () => {
  it('summarises a target diff as number tiles and a sample table', async () => {
    document.body.innerHTML = '<div id="out" hidden></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');

    PO.sync.renderTestResult(out, {
      role: 'target', problem: null, problem_text: null, would_add: 2, would_remove: 1, unchanged: 3,
      convert: 1, unsupported: 0,
      add_samples: [{ filename: 'a.jpg', media_item_id: 7, destination: '/OneDrive/Nathan/a.jpg' },
        { filename: 'b.heic', media_item_id: 8, destination: '/OneDrive/Chase/b.heic' }],
      remove_samples: [{ filename: 'For Mom/c.jpg', media_item_id: 9,
        destination: '/OneDrive/For Mom/c.jpg' }],
      records: [], more: false,
    }, i18n, window.APP_CONFIG);

    expect(out.hidden).toBe(false);
    expect(tiles(out)).toEqual([
      ['2', 'utilities:sync.test.stat.copy:2'], ['1', 'utilities:sync.test.stat.remove:1'],
      ['3', 'utilities:sync.test.stat.unchanged:3'], ['1', 'utilities:sync.test.stat.convert:1'],
    ]);
    expect(out.querySelector('.sync-test-nothing')).toBeNull();
    expect([...out.querySelectorAll('table.sync-test-table thead th')].map((th) => th.textContent)).toEqual([
      'utilities:sync.test.columns.file', 'utilities:sync.test.columns.destination',
      'utilities:sync.test.columns.action',
    ]);
    expect([...out.querySelectorAll('tbody tr')].map((tr) => [...tr.cells].map((cell) => cell.textContent))).toEqual([
      ['a.jpg', '/OneDrive/Nathan/a.jpg', 'utilities:sync.test.preview.copy'],
      ['b.heic', '/OneDrive/Chase/b.heic', 'utilities:sync.test.preview.copy'],
      ['For Mom/c.jpg', '/OneDrive/For Mom/c.jpg', 'utilities:sync.test.preview.remove'],
    ]);
    expect(out.querySelector('tbody .chip.chip-warning').textContent).toBe('utilities:sync.test.preview.remove');
    expect([...out.querySelectorAll('tbody td:first-child a')].map((link) => link.getAttribute('href')))
      .toEqual(['/media_view/media_item_id/7', '/media_view/media_item_id/8', '/media_view/media_item_id/9']);
    expect(out.querySelector('.sync-test-sample-note')).toBeNull();
  });

  it('says so in a sentence when a target already matches', async () => {
    document.body.innerHTML = '<div id="out"></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');

    PO.sync.renderTestResult(out, {
      role: 'target', problem: null, problem_text: null, would_add: 0, would_remove: 0, unchanged: 103,
      convert: 0, unsupported: 0, add_samples: [], remove_samples: [], records: [], more: false,
    }, i18n, window.APP_CONFIG);

    expect(out.querySelector('p.sync-test-nothing').textContent).toBe('utilities:sync.test.nothingToDo');
    expect(tiles(out)).toEqual([['103', 'utilities:sync.test.stat.unchanged:103']]);
    expect(out.querySelector('table')).toBeNull();
  });

  it('places the shared pagination controls after a target page', async () => {
    document.body.innerHTML = '<div id="out"></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');

    PO.sync.renderTestResult(out, {
      role: 'target', problem: null, problem_text: null, would_add: 103, would_remove: 0,
      unchanged: 0, convert: 0, unsupported: 0,
      add_samples: [{ filename: 'one.jpg', media_item_id: 1 }, { filename: 'two.jpg', media_item_id: 2 }],
      remove_samples: [], records: [], more: false,
      pagination_html: '<div class="pagination-container"><span class="results-count">Showing 1–2 of 103 results</span></div>',
    }, i18n, window.APP_CONFIG);

    expect(out.querySelectorAll('tbody tr')).toHaveLength(2);
    expect(out.querySelector('.pagination-container .results-count').textContent)
      .toBe('Showing 1–2 of 103 results');
  });

  it('counts a source page by what would happen to each file', async () => {
    document.body.innerHTML = '<div id="out"></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');

    PO.sync.renderTestResult(out, {
      role: 'source', problem: null, problem_text: null, would_add: 1, would_remove: 0, unchanged: 0,
      convert: 0, unsupported: 0, add_samples: [], remove_samples: [], more: true,
      state_counts: { new: 1, in_library: 1, imported: 1 }, total_items: 3, source_total: null,
      records: [{ filename: 'IMG_1.JPG', size: 2048, state: 'new', media_item_id: null },
        { filename: 'IMG_2.JPG', size: null, state: 'in_library', media_item_id: 9 },
        { filename: 'IMG_3.JPG', size: 10, state: 'imported', media_item_id: 10 }],
    }, i18n, window.APP_CONFIG);

    expect(tiles(out)).toEqual([
      ['1', 'utilities:sync.test.state.new:1'], ['1', 'utilities:sync.test.state.in_library:1'],
      ['1', 'utilities:sync.test.state.imported:1'],
    ]);
    expect(out.querySelector('.sync-test-nothing')).toBeNull();
    expect([...out.querySelectorAll('tbody tr')].map((tr) => [...tr.cells].map((td) => td.textContent))).toEqual([
      ['IMG_1.JPG', '2 KB', 'utilities:sync.test.state.new'],
      ['IMG_2.JPG', '', 'utilities:sync.test.state.in_library'],
      ['IMG_3.JPG', '10 B', 'utilities:sync.test.state.imported'],
    ]);
    expect(out.querySelector('tbody .chip.chip-accent').textContent).toBe('utilities:sync.test.state.new');
    expect([...out.querySelectorAll('tbody td:first-child a')].map((link) => link.textContent))
      .toEqual(['IMG_2.JPG', 'IMG_3.JPG']);
    expect(out.querySelector('.sync-test-note').textContent).toBe('utilities:sync.test.partial');
  });

  it('says how much of the source total a cut-short Test counted', async () => {
    document.body.innerHTML = '<div id="out"></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');
    const t = (key, options = {}) => (options.counted ? `${key}:${options.counted}/${options.total}` : key);

    PO.sync.renderTestResult(out, {
      role: 'source', problem: null, problem_text: null, would_add: 1, would_remove: 0, unchanged: 0,
      convert: 0, unsupported: 0, add_samples: [], remove_samples: [], more: true,
      state_counts: { new: 1 }, total_items: 400, source_total: 1342,
      records: [{ filename: 'IMG_1.JPG', size: 1, state: 'new', media_item_id: null }],
    }, { t }, window.APP_CONFIG);

    expect(out.querySelector('.sync-test-note').textContent)
      .toBe(`utilities:sync.test.partialOf:400/${new Intl.NumberFormat().format(1342)}`);
  });

  it('adds no note when the Test listed the whole source', async () => {
    document.body.innerHTML = '<div id="out"></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');

    PO.sync.renderTestResult(out, {
      role: 'source', problem: null, problem_text: null, would_add: 1, would_remove: 0, unchanged: 0,
      convert: 0, unsupported: 0, add_samples: [], remove_samples: [], more: false,
      state_counts: { new: 1 }, total_items: 1, source_total: 1,
      records: [{ filename: 'IMG_1.JPG', size: 1, state: 'new', media_item_id: null }],
    }, i18n, window.APP_CONFIG);

    expect(out.querySelector('.sync-test-note')).toBeNull();
  });

  it('says there is nothing new when a source page is all known', async () => {
    document.body.innerHTML = '<div id="out"></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');

    PO.sync.renderTestResult(out, {
      role: 'source', problem: null, problem_text: null, would_add: 0, would_remove: 0, unchanged: 0,
      convert: 0, unsupported: 0, add_samples: [], remove_samples: [], more: false,
      state_counts: { imported: 1 },
      records: [{ filename: 'a.png', size: 5, state: 'imported', media_item_id: 1 }],
    }, i18n, window.APP_CONFIG);

    expect(out.querySelector('.sync-test-nothing').textContent).toBe('utilities:sync.test.nothingNew');
  });

  it('shows the problem instead of a diff', async () => {
    document.body.innerHTML = '<div id="out"></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');

    PO.sync.renderTestResult(out, { role: 'target', problem: 'destination_missing',
      problem_text: 'The destination isn’t available.', records: [], add_samples: [], remove_samples: [] }, i18n, window.APP_CONFIG);

    expect(out.querySelector('.form-error').textContent).toBe('The destination isn’t available.');
    expect(out.querySelector('ul')).toBeNull();
  });
});

describe('sync page controls', () => {
  it('fills a folder option from the picker', async () => {
    document.body.innerHTML = `
      <input id="sync-option-folder" value="/old">
      <button type="button" data-folder-picker="sync-option-folder">Choose…</button>`;
    const PO = await loadModule('utilities/sync.js');
    PO.pickFolder = vi.fn(() => Promise.resolve('/Volumes/CARD/DCIM'));

    PO.sync.initSyncPage(window.testI18n, window.APP_CONFIG);
    document.querySelector('[data-folder-picker]').click();
    await Promise.resolve();

    expect(PO.pickFolder).toHaveBeenCalledWith({ mode: 'folder', startPath: '/old' });
    expect(document.getElementById('sync-option-folder').value).toBe('/Volumes/CARD/DCIM');
  });

  it('runs Test and renders the answer', async () => {
    document.body.innerHTML = `
      <button type="button" id="sync-test" data-test-url="/test">Test</button>
      <div id="sync-test-result" hidden></div>`;
    const PO = await loadModule('utilities/sync.js');
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({
      role: 'target', problem: null, problem_text: null, would_add: 0, would_remove: 0, unchanged: 4,
      convert: 0, unsupported: 0, add_samples: [], remove_samples: [], records: [], more: false }) }));

    PO.sync.initSyncPage(window.testI18n, window.APP_CONFIG);
    document.getElementById('sync-test').click();
    await vi.waitFor(() => expect(document.getElementById('sync-test-result').textContent)
      .toContain('utilities:sync.test.nothingToDo'));

    expect(global.fetch).toHaveBeenCalledWith('/test', { method: 'POST' });
    expect(document.getElementById('sync-test').disabled).toBe(false);
  });

  it('loads the next preview page from the shared pager', async () => {
    document.body.innerHTML = `
      <button type="button" id="sync-test" data-test-url="/test">Test</button>
      <div id="sync-test-result" hidden></div>`;
    const PO = await loadModule('utilities/sync.js');
    const base = { role: 'target', problem: null, problem_text: null, would_add: 11, would_remove: 0,
      unchanged: 0, convert: 0, unsupported: 0, remove_samples: [], records: [], more: false };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ...base,
        add_samples: [{ filename: 'first.jpg', media_item_id: 1 }],
        pagination_html: `<div class="pagination-container">
          <select data-pagination-size><option value="10" selected>10</option></select>
          <button type="button" data-pagination-page="1">Next</button></div>`,
      }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ...base,
        add_samples: [{ filename: 'last.jpg', media_item_id: 11 }], pagination_html: '',
      }) });
    vi.stubGlobal('fetch', fetchMock);

    PO.sync.initSyncPage(window.testI18n, window.APP_CONFIG);
    document.getElementById('sync-test').click();
    await vi.waitFor(() => expect(document.querySelector('[data-pagination-page="1"]')).not.toBeNull());
    document.querySelector('[data-pagination-page="1"]').click();
    await vi.waitFor(() => expect(document.getElementById('sync-test-result').textContent).toContain('last.jpg'));

    expect(fetchMock).toHaveBeenLastCalledWith('/test', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page: 1, page_size: 10 }),
    });
  });
});

describe('New automation type choice', () => {
  it('enables only the chosen direction’s services and hint', async () => {
    document.body.innerHTML = `
      <div id="modal">
        <input type="radio" name="kind" value="custom" checked>
        <input type="radio" name="kind" value="send">
        <div data-sync-services="import"><input type="radio" name="adapter_id" value="1"></div>
        <div data-sync-services="send"><input type="radio" name="adapter_id" value="2"></div>
        <p data-kind-hint="custom"></p><p data-kind-hint="send"></p>
      </div>`;
    const PO = await loadModule('utilities/_base.js');
    const modal = document.getElementById('modal');

    PO.utilities.initNewAutomationKinds(modal);
    const send = modal.querySelector('[data-sync-services="send"]');
    expect(send.hidden).toBe(true);
    expect(send.querySelector('input').disabled).toBe(true);

    const radio = modal.querySelector('input[value="send"]');
    radio.checked = true;
    radio.dispatchEvent(new Event('change'));

    expect(send.hidden).toBe(false);
    expect(send.querySelector('input').disabled).toBe(false);
    expect(modal.querySelector('[data-sync-services="import"] input').disabled).toBe(true);
    expect(modal.querySelector('[data-kind-hint="send"]').hidden).toBe(false);
    expect(modal.querySelector('[data-kind-hint="custom"]').hidden).toBe(true);
  });
});

const pickerHtml = `
  <form id="options">
    <div data-remote-option="folder" data-choices-url="/choices?option=folder">
      <input type="hidden" name="folder" value="">
      <div class="sync-folder-input">
        <input type="text" class="sync-remote-value" readonly value="" data-remote-open>
        <button type="button" data-remote-open>Choose</button>
      </div>
      <div data-remote-picker hidden>
        <nav data-remote-crumbs></nav>
        <ul data-remote-list></ul>
        <p data-remote-status></p>
        <button type="button" data-remote-more hidden>more</button>
        <button type="button" data-remote-close>cancel</button>
        <button type="button" data-remote-choose-here hidden>here</button>
      </div>
    </div>
  </form>`;

const flush = () => new Promise((resolve) => { setTimeout(resolve, 0); });

describe('remote folder picker', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('browses into folders and stores the chosen path as id and label', async () => {
    document.body.innerHTML = pickerHtml;
    const PO = await loadModule('utilities/sync.js');
    const answers = {
      '/choices?option=folder&': { items: [
        { id: 'CAMROLL', label: 'Camera Roll', has_children: true, suggested: true },
        { id: 'DOCS', label: 'Documents', has_children: false },
      ], next_cursor: null },
      '/choices?option=folder&parent=CAMROLL': { items: [{ id: 'Y24', label: '2024', has_children: false }] },
    };
    const fetchMock = vi.fn(async (url) => ({ ok: true, json: async () => answers[url] }));
    vi.stubGlobal('fetch', fetchMock);
    const form = document.getElementById('options');
    const submitted = vi.fn();
    form.requestSubmit = submitted;

    PO.sync.initRemoteOption(document.querySelector('[data-remote-option]'), i18n);
    document.querySelector('button[data-remote-open]').click();
    await flush();
    const picker = document.querySelector('[data-remote-picker]');
    expect(picker.hidden).toBe(false);
    const rows = [...document.querySelectorAll('.sync-picker-item')];
    expect(rows.map((row) => row.querySelector('.sync-picker-name').textContent)).toEqual(['Camera Roll', 'Documents']);
    expect(rows[0].textContent).toContain('utilities:sync.picker.suggested');
    // Only folders with subfolders can be opened; every row can be chosen.
    expect(rows[0].querySelector('button.sync-picker-open')).not.toBeNull();
    expect(rows[1].querySelector('button.sync-picker-open')).toBeNull();
    expect(document.querySelector('[data-remote-crumbs]').textContent).toBe('utilities:sync.picker.top');

    rows[0].querySelector('.sync-picker-open').click();
    await flush();
    expect(fetchMock).toHaveBeenLastCalledWith('/choices?option=folder&parent=CAMROLL');
    expect(document.querySelector('[data-remote-choose-here]').hidden).toBe(false);
    expect([...document.querySelectorAll('[data-remote-crumbs] > *')].map((el) => el.textContent))
      .toEqual(['utilities:sync.picker.top', '›', 'Camera Roll']);
    document.querySelector('.sync-picker-item .btn-sm').click();  // Choose "2024"

    expect(JSON.parse(document.querySelector('input[name="folder"]').value))
      .toEqual({ id: 'Y24', label: 'Camera Roll › 2024' });
    expect(document.querySelector('.sync-remote-value').value).toBe('Camera Roll › 2024');
    expect(picker.hidden).toBe(true);
    expect(submitted).toHaveBeenCalledTimes(1);
  });

  it('shows the server error instead of a list', async () => {
    document.body.innerHTML = pickerHtml;
    const PO = await loadModule('utilities/sync.js');
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({ error: 'Sign in first.' }) })));
    PO.sync.initRemoteOption(document.querySelector('[data-remote-option]'), i18n);
    document.querySelector('.sync-remote-value').click();
    await flush();
    expect(document.querySelector('[data-remote-status]').textContent).toBe('Sign in first.');
    expect(document.querySelectorAll('.sync-picker-item')).toHaveLength(0);
  });
});

describe('device-code sign-in', () => {
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

  it('shows the code and link, then polls until signed in', async () => {
    document.body.innerHTML = `
      <details id="dc" data-start-url="/start" data-poll-url="/poll">
        <button type="button" data-device-start>go</button>
        <div data-device-box hidden><a data-device-link href="#"></a><p data-device-code></p></div>
        <p data-device-status></p>
      </details>`;
    const PO = await loadModule('utilities/sync.js');
    const replies = [{ status: 'pending' }, { status: 'done' }];
    const fetchMock = vi.fn(async (url) => ({
      ok: true,
      json: async () => (url === '/start'
        ? { handle: 'h1', user_code: 'ABCD1234', verification_uri: 'https://microsoft.com/devicelogin', interval: 2 }
        : replies.shift()),
    }));
    vi.stubGlobal('fetch', fetchMock);
    const reload = vi.fn();
    vi.stubGlobal('location', { ...window.location, reload });
    vi.useFakeTimers();

    PO.sync.initDeviceCode(document.getElementById('dc'), i18n);
    document.querySelector('[data-device-start]').click();
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[data-device-code]').textContent).toBe('ABCD1234');
    expect(document.querySelector('[data-device-link]').getAttribute('href')).toBe('https://microsoft.com/devicelogin');
    expect(document.querySelector('[data-device-box]').hidden).toBe(false);
    await vi.advanceTimersByTimeAsync(2000);
    expect(fetchMock.mock.calls.filter(([url]) => url === '/poll')).toHaveLength(2);
    expect(reload).toHaveBeenCalled();
  });
});

describe('the service builder page', () => {
  const pageWith = (html) => {
    document.body.innerHTML = html;
    const chat = [];
    window.PHOTO_ORGANIZER.COMPONENTS = {
      initChatDialog: (id, options) => {
        const api = { load: vi.fn(), enterRunning: vi.fn() };
        chat.push({ id, options, api });
        return api;
      },
      modal: { init: () => ({ open: vi.fn() }) },
    };
    return chat;
  };

  it('polls and posts to the service builder routes', async () => {
    const PO = await loadModule('utilities/sync.js');
    const chat = pageWith('<div id="service-chat"></div>');
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal('fetch', fetchMock);

    PO.sync.initServicePage('custom.my-immich', 'READY', i18n, window.APP_CONFIG);

    expect(chat).toHaveLength(1);
    const { options } = chat[0];
    expect(chat[0].id).toBe('service-chat');
    expect(options.statusUrl()).toBe('/sync_service_status/slug/custom.my-immich');
    expect(await options.onSend('Import from my Immich')).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledWith('/sync_service_chat/slug/custom.my-immich', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ message: 'Import from my Immich' }),
    }));
    vi.unstubAllGlobals();
  });

  it('reloads only after a run it followed, never on the first poll of an idle builder', async () => {
    const PO = await loadModule('utilities/sync.js');
    const chat = pageWith('<div id="service-chat"></div>');
    const reload = vi.fn();
    vi.stubGlobal('location', { ...window.location, reload });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }));
    PO.sync.initServicePage('s', 'READY', i18n, window.APP_CONFIG);
    const { options } = chat[0];

    options.onStatus({ status: 'READY', messages: [] });
    options.onSettled({ status: 'READY', messages: [] });
    expect(reload).not.toHaveBeenCalled();

    await options.onSend('Send albums to Flickr');
    options.onStatus({ status: 'IN_PROGRESS', messages: [] });
    options.onSettled({ status: 'READY', messages: [] });
    expect(reload).toHaveBeenCalledTimes(1);

    options.onSettled({ status: 'READY', messages: [] });        // a later idle poll
    expect(reload).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it('reports a refused message', async () => {
    const PO = await loadModule('utilities/sync.js');
    const chat = pageWith('<div id="service-chat"></div>');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({ error: 'busy' }) }));

    PO.sync.initServicePage('s', 'READY', i18n, window.APP_CONFIG);

    expect(await chat[0].options.onSend('x')).toEqual({ ok: false, error: 'busy' });
    vi.unstubAllGlobals();
  });

  it('opens the publish review', async () => {
    const PO = await loadModule('utilities/sync.js');
    pageWith('<button id="service-publish"></button><div id="servicePublishModal"></div>');
    const open = vi.fn();
    window.PHOTO_ORGANIZER.COMPONENTS.modal = { init: () => ({ open }) };

    PO.sync.initServicePage('s', 'READY', i18n, window.APP_CONFIG);
    document.getElementById('service-publish').click();

    expect(open).toHaveBeenCalled();
  });

  it('deletes only after the confirm dialog agrees', async () => {
    const PO = await loadModule('utilities/sync.js');
    pageWith('<form data-confirm-delete="x"><button type="submit">Delete</button></form>');
    const form = document.querySelector('form');
    const submit = vi.spyOn(form, 'submit').mockImplementation(() => {});
    window.PHOTO_ORGANIZER.confirmDialog = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);

    PO.sync.initServicePage('s', 'READY', i18n, window.APP_CONFIG);
    form.dispatchEvent(new Event('submit', { cancelable: true }));
    await Promise.resolve(); await Promise.resolve();
    expect(submit).not.toHaveBeenCalled();
    form.dispatchEvent(new Event('submit', { cancelable: true }));
    await Promise.resolve(); await Promise.resolve();
    expect(submit).toHaveBeenCalledTimes(1);
    expect(window.PHOTO_ORGANIZER.confirmDialog).toHaveBeenCalledWith(expect.objectContaining({
      title: 'utilities:sync.service.deleteTitle', confirmClass: 'btn-danger',
    }));
  });

  const docsRequest = (status = 'pending', extra = {}) => ({
    id: 7, type: 'permission',
    content: JSON.stringify({ kind: 'docs', status, domains: {
      'immich.app': ['https://immich.app/docs', 'https://immich.app/openapi.json'],
      'example.org': ['https://example.org/x'] }, allowed: [], denied: [], ...extra }),
  });

  it('draws a docs request as a card and sends only the ticked domains', async () => {
    const PO = await loadModule('utilities/sync.js');
    const chat = pageWith('<div id="service-chat"></div>');
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ started: true }) });
    vi.stubGlobal('fetch', fetchMock);
    PO.sync.initServicePage('s', 'READY', i18n, window.APP_CONFIG);
    const { options, api } = chat[0];
    expect(api.load).toHaveBeenCalled();

    const [card] = options.renderMessages([docsRequest()]);
    document.body.append(card);
    const boxes = [...card.querySelectorAll('input[type="checkbox"]')];
    expect(boxes.map((box) => box.value)).toEqual(['immich.app', 'example.org']);
    expect(card.querySelectorAll('.service-permission-urls li')).toHaveLength(3);
    boxes[1].checked = false;
    card.querySelector('.btn-primary').click();
    await vi.waitFor(() => expect(api.enterRunning).toHaveBeenCalled());

    expect(fetchMock).toHaveBeenCalledWith('/sync_service_permission/slug/s/message_id/7', expect.objectContaining({
      body: JSON.stringify({ decision: 'allow', domains: ['immich.app'] }),
    }));
    expect([...card.querySelectorAll('button')].every((button) => button.disabled)).toBe(true);
    vi.unstubAllGlobals();
  });

  it('answers a live-reads request and reloads when nothing started', async () => {
    const PO = await loadModule('utilities/sync.js');
    const chat = pageWith('<div id="service-chat"></div>');
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ started: false }) });
    vi.stubGlobal('fetch', fetchMock);
    PO.sync.initServicePage('s', 'READY', i18n, window.APP_CONFIG);
    const { options, api } = chat[0];

    const [card] = options.renderMessages([{ id: 9, type: 'permission',
      content: JSON.stringify({ kind: 'live_reads', status: 'pending', account: 'jason@example.com' }) }]);
    card.querySelector('.btn-secondary').click();
    await vi.waitFor(() => expect(api.load).toHaveBeenCalledTimes(2));

    expect(card.textContent).toContain('utilities:sync.service.liveAsk');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ decision: 'deny' });
    vi.unstubAllGlobals();
  });

  it('shows an answered request as one line, and other entries as bubbles', async () => {
    const PO = await loadModule('utilities/sync.js');
    const chat = pageWith('<div id="service-chat"></div>');
    PO.sync.initServicePage('s', 'READY', i18n, window.APP_CONFIG);

    const [answered, bubble] = chat[0].options.renderMessages([
      docsRequest('partial', { allowed: ['immich.app'], denied: ['example.org'] }),
      { id: 8, type: 'assistant', content: 'Drafted.' }]);

    expect(answered.classList.contains('is-answered')).toBe(true);
    expect(answered.querySelector('button')).toBeNull();
    expect(answered.textContent).toBe('utilities:sync.service.docsAllowed utilities:sync.service.docsDenied');
    expect(bubble.className).toBe('chat-message chat-message-assistant');
    expect(bubble.textContent).toBe('Drafted.');
  });
});

describe('a server address', () => {
  it('asks to confirm http only for an http:// address', async () => {
    const PO = await loadModule('utilities/sync.js');
    document.body.innerHTML = `<div data-server-address><input data-base-url value="">
      <label data-allow-http><input type="checkbox" name="allow_http"></label></div>`;
    window.PHOTO_ORGANIZER.COMPONENTS = {};
    const input = document.querySelector('[data-base-url]');
    const confirm = document.querySelector('[data-allow-http]');
    const box = confirm.querySelector('input');

    PO.sync.initServicePage('s', 'READY', { t: (k) => k }, window.APP_CONFIG);
    expect(confirm.hidden).toBe(true);

    input.value = 'HTTP://192.168.1.20:2283';
    input.dispatchEvent(new Event('input'));
    expect(confirm.hidden).toBe(false);
    box.checked = true;

    input.value = 'photos.example.com';
    input.dispatchEvent(new Event('input'));
    expect(confirm.hidden).toBe(true);
    expect(box.checked).toBe(false);
  });
});
