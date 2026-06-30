import { loadModule } from '../support/load_module.js';

// initIndexPhotos streams the media scan as NDJSON: `progress` records bump the
// live counter, the final `done` record fills the stats + tables and reveals the
// Sync button, `error` records surface a notification. These tests drive runScan
// against a stubbed fetch whose body streams chosen chunks, exercising the
// buffer-splitting (partial lines, trailing record without a newline) and the
// per-record DOM side effects.

const SCAN_URL = '/utilities/index-photos/scan';

const config = { urls: { utilities_index_photos_scan: SCAN_URL } };

const opts = { canScan: false, canSync: true, hasActiveJobs: false, mediaDirs: [] };

const STAT_IDS = [
  'stat-total-filesystem',
  'stat-total-imported',
  'stat-total-indexed',
  'stat-unindexed',
  'stat-orphaned',
];

const fixture = () => {
  document.body.innerHTML = `
    <p id="scan-status" hidden></p>
    ${STAT_IDS.map((id) => `<span id="${id}"></span>`).join('')}
    <button id="sync-button" hidden></button>
    <div id="scan-results"></div>`;
};

// Build a Response-like object whose body streams the given string chunks as
// UTF-8, matching the reader interface runScan consumes.
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

const stubFetch = (response) => {
  const fetchMock = vi.fn(() => Promise.resolve(response));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
};

const init = async () => (
  await loadModule('utilities/index_photos.js')
).indexPhotos.init(opts, window.testI18n, config);

const stat = (id) => document.getElementById(id).textContent;

const DONE_RECORD = {
  type: 'done',
  total_filesystem: 100,
  total_imported: 90,
  total_indexed: 80,
  unindexed: [{ filename: 'a.jpg', full_path: '/media/a.jpg' }],
  orphaned: [{ id: 7, reason: 'missing', full_path: '/media/old.jpg' }],
};

beforeEach(fixture);
afterEach(() => vi.unstubAllGlobals());

describe('initIndexPhotos runScan — record dispatch', () => {
  it('updates the filesystem counter on a progress record', async () => {
    stubFetch(streamResponse(['{"type":"progress","scanned":5}\n']));
    const api = await init();
    await api.runScan();
    expect(stat('stat-total-filesystem')).toBe('5');
  });

  it('fills every stat and reveals an enabled Sync button on done', async () => {
    stubFetch(streamResponse([JSON.stringify(DONE_RECORD) + '\n']));
    const api = await init();
    await api.runScan();

    expect(stat('stat-total-filesystem')).toBe('100');
    expect(stat('stat-total-imported')).toBe('90');
    expect(stat('stat-total-indexed')).toBe('80');
    expect(stat('stat-unindexed')).toBe('1');
    expect(stat('stat-orphaned')).toBe('1');

    const syncButton = document.getElementById('sync-button');
    expect(syncButton.hidden).toBe(false);
    expect(syncButton.disabled).toBe(false);

    // Both an unindexed and an orphaned section rendered into the results container.
    expect(document.querySelectorAll('#scan-results table').length).toBe(2);
  });

  it('keeps Sync hidden and shows the in-sync state when there is no work', async () => {
    const inSync = { ...DONE_RECORD, unindexed: [], orphaned: [] };
    stubFetch(streamResponse([JSON.stringify(inSync) + '\n']));
    const api = await init();
    await api.runScan();

    expect(document.getElementById('sync-button').hidden).toBe(true);
    expect(document.querySelectorAll('#scan-results table').length).toBe(0);
    expect(document.querySelector('#scan-results .empty-state')).not.toBeNull();
  });

  it('surfaces an error notification on an error record', async () => {
    stubFetch(streamResponse(['{"type":"error","message":"disk gone"}\n']));
    const api = await init();
    await api.runScan();
    expect(window.notification.error).toHaveBeenCalledTimes(1);
  });
});

describe('initIndexPhotos runScan — NDJSON buffer handling', () => {
  it('reassembles a record split across two chunks', async () => {
    stubFetch(streamResponse(['{"type":"progr', 'ess","scanned":42}\n']));
    const api = await init();
    await api.runScan();
    expect(stat('stat-total-filesystem')).toBe('42');
  });

  it('processes a trailing record that has no terminating newline', async () => {
    // No '\n' — exercises the post-loop `tail` branch.
    stubFetch(streamResponse([JSON.stringify(DONE_RECORD)]));
    const api = await init();
    await api.runScan();
    expect(stat('stat-total-filesystem')).toBe('100');
  });

  it('handles several records arriving in a single chunk', async () => {
    const chunk =
      '{"type":"progress","scanned":1}\n' +
      '{"type":"progress","scanned":2}\n' +
      JSON.stringify(DONE_RECORD) + '\n';
    stubFetch(streamResponse([chunk]));
    const api = await init();
    await api.runScan();
    // Last write wins: the done record's total overrides the progress counter.
    expect(stat('stat-total-filesystem')).toBe('100');
  });
});

describe('initIndexPhotos runScan — request failure', () => {
  it('notifies and clears status when the response is not ok', async () => {
    stubFetch(streamResponse([], { ok: false }));
    const api = await init();
    await api.runScan();
    expect(window.notification.error).toHaveBeenCalledTimes(1);
    expect(document.getElementById('scan-status').hidden).toBe(true);
  });
});
