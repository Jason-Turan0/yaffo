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
      convert: 1, unsupported: 0, add_samples: ['a.jpg', 'b.heic'], remove_samples: ['For Mom/c.jpg'],
      records: [], more: false,
    }, i18n);

    expect(out.hidden).toBe(false);
    expect(tiles(out)).toEqual([
      ['2', 'utilities:sync.test.stat.copy:2'], ['1', 'utilities:sync.test.stat.remove:1'],
      ['3', 'utilities:sync.test.stat.unchanged:3'], ['1', 'utilities:sync.test.stat.convert:1'],
    ]);
    expect(out.querySelector('.sync-test-nothing')).toBeNull();
    expect([...out.querySelectorAll('table.sync-test-table thead th')].map((th) => th.textContent)).toEqual([
      'utilities:sync.test.columns.file', 'utilities:sync.test.columns.action',
    ]);
    expect([...out.querySelectorAll('tbody tr')].map((tr) => [...tr.cells].map((cell) => cell.textContent))).toEqual([
      ['a.jpg', 'utilities:sync.test.preview.copy'],
      ['b.heic', 'utilities:sync.test.preview.copy'],
      ['For Mom/c.jpg', 'utilities:sync.test.preview.remove'],
    ]);
    expect(out.querySelector('tbody .chip.chip-warning').textContent).toBe('utilities:sync.test.preview.remove');
    expect(out.querySelector('.sync-test-sample-note')).toBeNull();
  });

  it('says so in a sentence when a target already matches', async () => {
    document.body.innerHTML = '<div id="out"></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');

    PO.sync.renderTestResult(out, {
      role: 'target', problem: null, problem_text: null, would_add: 0, would_remove: 0, unchanged: 103,
      convert: 0, unsupported: 0, add_samples: [], remove_samples: [], records: [], more: false,
    }, i18n);

    expect(out.querySelector('p.sync-test-nothing').textContent).toBe('utilities:sync.test.nothingToDo');
    expect(tiles(out)).toEqual([['103', 'utilities:sync.test.stat.unchanged:103']]);
    expect(out.querySelector('table')).toBeNull();
  });

  it('labels a partial target preview with the full planned count', async () => {
    document.body.innerHTML = '<div id="out"></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');

    PO.sync.renderTestResult(out, {
      role: 'target', problem: null, problem_text: null, would_add: 103, would_remove: 0,
      unchanged: 0, convert: 0, unsupported: 0,
      add_samples: ['one.jpg', 'two.jpg'], remove_samples: [], records: [], more: false,
    }, i18n);

    expect(out.querySelectorAll('tbody tr')).toHaveLength(2);
    expect(out.querySelector('.sync-test-sample-note').textContent)
      .toBe('utilities:sync.test.preview.showing:2/103');
  });

  it('counts a source page by what would happen to each file', async () => {
    document.body.innerHTML = '<div id="out"></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');

    PO.sync.renderTestResult(out, {
      role: 'source', problem: null, problem_text: null, would_add: 1, would_remove: 0, unchanged: 0,
      convert: 0, unsupported: 0, add_samples: [], remove_samples: [], more: true,
      records: [{ filename: 'IMG_1.JPG', size: 2048, state: 'new' },
        { filename: 'IMG_2.JPG', size: null, state: 'in_library' },
        { filename: 'IMG_3.JPG', size: 10, state: 'imported' }],
    }, i18n);

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
    expect(out.textContent).toContain('utilities:sync.test.more');
  });

  it('says there is nothing new when a source page is all known', async () => {
    document.body.innerHTML = '<div id="out"></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');

    PO.sync.renderTestResult(out, {
      role: 'source', problem: null, problem_text: null, would_add: 0, would_remove: 0, unchanged: 0,
      convert: 0, unsupported: 0, add_samples: [], remove_samples: [], more: false,
      records: [{ filename: 'a.png', size: 5, state: 'imported' }],
    }, i18n);

    expect(out.querySelector('.sync-test-nothing').textContent).toBe('utilities:sync.test.nothingNew');
  });

  it('shows the problem instead of a diff', async () => {
    document.body.innerHTML = '<div id="out"></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');

    PO.sync.renderTestResult(out, { role: 'target', problem: 'destination_missing',
      problem_text: 'The destination isn’t available.', records: [], add_samples: [], remove_samples: [] }, i18n);

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
