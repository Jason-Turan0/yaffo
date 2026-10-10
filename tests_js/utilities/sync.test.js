import { loadModule } from '../support/load_module.js';

const i18n = { t: (key, options = {}) => {
  if (options.count !== undefined) return `${key}:${options.count}`;
  if (options.shown !== undefined) return `${key}:${options.shown}/${options.total}`;
  return key;
} };

describe('sync Test results', () => {
  it('summarises a target diff and says nothing changed', async () => {
    document.body.innerHTML = '<div id="out" hidden></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');

    PO.sync.renderTestResult(out, {
      role: 'target', problem: null, problem_text: null, would_add: 2, would_remove: 1, unchanged: 3,
      convert: 1, unsupported: 0, add_samples: ['a.jpg', 'b.heic'], remove_samples: ['For Mom/c.jpg'],
      records: [], more: false,
    }, i18n);

    expect(out.hidden).toBe(false);
    expect([...out.querySelectorAll('.sync-test-stat')].map((stat) => stat.textContent)).toEqual([
      'utilities:sync.test.wouldAdd:2', 'utilities:sync.test.wouldRemove:1',
      'utilities:sync.test.unchanged:3', 'utilities:sync.test.convert:1',
    ]);
    expect([...out.querySelectorAll('.table-container > table.data-table tbody tr')].map((tr) =>
      [...tr.cells].map((cell) => cell.textContent))).toEqual([
      ['a.jpg', 'utilities:sync.test.preview.copy'],
      ['b.heic', 'utilities:sync.test.preview.copy'],
      ['For Mom/c.jpg', 'utilities:sync.test.preview.remove'],
    ]);
    expect(out.querySelector('.sync-test-sample-note')).toBeNull();
    expect(out.textContent).toContain('utilities:sync.test.nothingChanged');
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

  it('lists a source page with what would happen to each file', async () => {
    document.body.innerHTML = '<div id="out"></div>';
    const PO = await loadModule('utilities/sync.js');
    const out = document.getElementById('out');

    PO.sync.renderTestResult(out, {
      role: 'source', problem: null, problem_text: null, would_add: 1, would_remove: 0, unchanged: 0,
      convert: 0, unsupported: 0, add_samples: [], remove_samples: [], more: true,
      records: [{ filename: 'IMG_1.JPG', size: 2048, state: 'new' },
                { filename: 'IMG_2.JPG', size: null, state: 'in_library' }],
    }, i18n);

    const cells = [...out.querySelectorAll('tbody tr, tr')].slice(1).map((tr) =>
      [...tr.children].map((td) => td.textContent));
    expect(cells).toEqual([
      ['IMG_1.JPG', '2 KB', 'utilities:sync.test.state.new'],
      ['IMG_2.JPG', '', 'utilities:sync.test.state.in_library'],
    ]);
    expect(out.querySelector('.table-container > table.data-table')).not.toBeNull();
    expect(out.textContent).toContain('utilities:sync.test.more');
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
