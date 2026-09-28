import { loadModule } from '../support/load_module.js';

// initIndexPhotos streams the media scan as NDJSON: `progress` records bump the
// live counter, the final `done` record fills the stats and the Library status
// cards (one per problem, each with its own fix), `error` records surface a
// notification. "Everything is in sync" shows only when no card applies. These
// tests drive runScan against a stubbed fetch whose body streams chosen chunks,
// exercising the buffer-splitting (partial lines, trailing record without a
// newline) and the per-record DOM side effects, then each card's fix.

const SCAN_URL = '/library/health/scan';
const SYNC_URL = '/library/health/sync';
const RETRY_URL = '/library/health/retry-failed';
const REGEN_URL = '/library/health/regenerate-thumbnails';

const config = {
  urls: {
    utilities_index_photos_scan: SCAN_URL,
    utilities_sync_photos: SYNC_URL,
    utilities_retry_failed: RETRY_URL,
    utilities_regenerate_thumbnails: REGEN_URL,
  },
};

const opts = { canScan: false, canSync: true, hasActiveJobs: false, failedCount: 0, mediaDirs: [] };

const STAT_IDS = [
  'stat-total-filesystem',
  'stat-total-imported',
  'stat-total-indexed',
  'stat-unindexed',
  'stat-orphaned',
  'stat-missing-thumbnails',
];

// Mirrors components/issue_card.html as templates/utilities/index_photos.html uses it.
const card = (key, action, { files = true, shown = false } = {}) => `
  <div class="issue-card" id="issue-${key}" ${shown ? '' : 'hidden'}>
    <h3 id="issue-${key}-title"></h3>
    <button id="${action}">${action}</button>
    ${files ? `<details><summary>Show files</summary><div id="issue-${key}-files"></div></details>` : ''}
  </div>`;

const fixture = ({ failed = false } = {}) => {
  document.body.innerHTML = `
    <button id="reindex-button">Reindex Library</button>
    ${STAT_IDS.map((id) => `<span id="${id}"></span>`).join('')}
    <p id="scan-status" hidden></p>
    <div id="scan-warnings" hidden></div>
    <div id="status-in-sync" hidden><h3>Everything is in sync</h3></div>
    <div id="scan-results">
      ${card('unindexed', 'index-new-button')}
      ${card('orphaned', 'remove-orphaned-button')}
      ${card('missing-thumbnails', 'regenerate-thumbnails-button', { files: false })}
      ${card('failed', 'retry-failed-button', { shown: failed })}
    </div>`;
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

const init = async (overrides = {}) => (
  await loadModule('utilities/index_photos.js')
).indexPhotos.init({ ...opts, ...overrides }, window.testI18n, config);

const scan = async (record, overrides) => {
  stubFetch(streamResponse([JSON.stringify(record) + '\n']));
  const api = await init(overrides);
  await api.runScan();
  return api;
};

const stat = (id) => document.getElementById(id).textContent;
const isShown = (id) => !document.getElementById(id).hidden;
const byId = (id) => document.getElementById(id);

const DONE_RECORD = {
  type: 'done',
  total_filesystem: 100,
  total_imported: 90,
  total_indexed: 80,
  unindexed: [{ filename: 'a.jpg', full_path: '/media/a.jpg' }],
  orphaned: [{ id: 7, reason: 'missing', full_path: '/media/old.jpg' }],
  empty_roots: [],
  missing_thumbnails: 0,
};

const CLEAN = { ...DONE_RECORD, unindexed: [], orphaned: [] };

beforeEach(() => fixture());
afterEach(() => vi.unstubAllGlobals());

describe('initIndexPhotos runScan — record dispatch', () => {
  it('updates the filesystem counter on a progress record', async () => {
    stubFetch(streamResponse(['{"type":"progress","scanned":5}\n']));
    const api = await init();
    await api.runScan();
    expect(stat('stat-total-filesystem')).toBe('5');
  });

  it('fills every stat and shows a card per problem with an enabled fix', async () => {
    await scan({ ...DONE_RECORD, missing_thumbnails: 1409 });

    expect(stat('stat-total-filesystem')).toBe('100');
    expect(stat('stat-total-imported')).toBe('90');
    expect(stat('stat-total-indexed')).toBe('80');
    expect(stat('stat-unindexed')).toBe('1');
    expect(stat('stat-orphaned')).toBe('1');
    expect(stat('stat-missing-thumbnails')).toBe(window.testI18n.number(1409));

    for (const [key, action] of [['unindexed', 'index-new-button'], ['orphaned', 'remove-orphaned-button'],
      ['missing-thumbnails', 'regenerate-thumbnails-button']]) {
      expect(isShown(`issue-${key}`)).toBe(true);
      expect(byId(action).disabled).toBe(false);
    }
    expect(stat('issue-missing-thumbnails-title')).toBe(window.testI18n.t(
      'utilities:indexPhotos.missingThumbnails.title', { count: 1409, formattedCount: window.testI18n.number(1409) }));
    // Each file list folds under its own card.
    expect(byId('issue-unindexed-files').querySelectorAll('tbody tr').length).toBe(1);
    expect(byId('issue-orphaned-files').querySelectorAll('tbody tr').length).toBe(1);
    expect(isShown('status-in-sync')).toBe(false);
  });

  it('calls the library in sync only when nothing needs attention', async () => {
    await scan(CLEAN);

    expect(isShown('status-in-sync')).toBe(true);
    for (const key of ['unindexed', 'orphaned', 'missing-thumbnails', 'failed']) {
      expect(isShown(`issue-${key}`)).toBe(false);
    }
  });

  it.each([
    ['missing thumbnails', { missing_thumbnails: 3 }, {}],
    ['a thumbnail folder that could not be checked', { missing_thumbnails: null }, {}],
    ['an empty media folder', { empty_roots: ['/Volumes/Photos'] }, {}],
    ['files that could not be indexed', {}, { failedCount: 2 }],
  ])('is not in sync with %s', async (_, record, overrides) => {
    if (overrides.failedCount) fixture({ failed: true });
    await scan({ ...CLEAN, ...record }, overrides);

    expect(isShown('status-in-sync')).toBe(false);
  });

  it('warns about media folders that hold no media files', async () => {
    await scan({ ...DONE_RECORD, empty_roots: ['/Volumes/Photos'] });

    const warnings = byId('scan-warnings');
    expect(warnings.hidden).toBe(false);
    expect(warnings.querySelector('.alert-warning').textContent).toBe('utilities:indexPhotos.emptyFolders');
  });

  it('shows a dash and a warning when the thumbnail folder cannot be checked', async () => {
    await scan({ ...CLEAN, missing_thumbnails: null });

    expect(stat('stat-missing-thumbnails')).toBe('—');
    expect(isShown('issue-missing-thumbnails')).toBe(false);
    expect(byId('scan-warnings').textContent).toBe(
      window.testI18n.t('utilities:indexPhotos.thumbnailFolderUnavailable'));
  });

  it('shows no warning when every folder holds media and thumbnails were checked', async () => {
    await scan(DONE_RECORD);
    expect(byId('scan-warnings').hidden).toBe(true);
  });

  it('disables the fixes while an index job is running', async () => {
    await scan({ ...DONE_RECORD, missing_thumbnails: 3 }, { hasActiveJobs: true });

    for (const action of ['index-new-button', 'remove-orphaned-button', 'regenerate-thumbnails-button']) {
      expect(byId(action).disabled).toBe(true);
    }
  });

  it('surfaces an error notification on an error record', async () => {
    stubFetch(streamResponse(['{"type":"error","message":"disk gone"}\n']));
    const api = await init();
    await api.runScan();
    expect(window.notification.failure).toHaveBeenCalledTimes(1);
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
    expect(window.notification.failure).toHaveBeenCalledTimes(1);
    expect(byId('scan-status').hidden).toBe(true);
  });
});

describe('initIndexPhotos — each card fixes only its own problem', () => {
  const postedBody = (fetchMock) => JSON.parse(fetchMock.mock.calls.at(-1)[1].body);

  it('indexes the new files without removing the orphaned entries', async () => {
    const api = await scan(DONE_RECORD);
    const fetchMock = stubFetch({ ok: true, json: () => Promise.resolve({ job_id: 'j' }) });

    await api.startIndexNew();

    expect(fetchMock.mock.calls.at(-1)[0]).toBe(SYNC_URL);
    expect(postedBody(fetchMock)).toEqual({ files_to_index: ['/media/a.jpg'], files_to_delete: [] });
    expect(window.notification.success).toHaveBeenCalledWith(
      window.testI18n.t('utilities:indexPhotos.indexNew.started', {}));
  });

  it('removes the orphaned entries without indexing the new files', async () => {
    const api = await scan(DONE_RECORD);
    const fetchMock = stubFetch({ ok: true, json: () => Promise.resolve({ job_id: 'j' }) });

    await api.startRemoveOrphaned();

    expect(postedBody(fetchMock)).toEqual({ files_to_index: [], files_to_delete: [7] });
  });

  it('retries the failed files and announces the count', async () => {
    const fetchMock = stubFetch({ ok: true, json: () => Promise.resolve({ media_item_count: 67 }) });
    const api = await init();

    await api.startRetryFailed();

    expect(fetchMock).toHaveBeenCalledWith(RETRY_URL, { method: 'POST' });
    expect(window.notification.success).toHaveBeenCalledWith(
      window.testI18n.t('utilities:indexPhotos.retryFailed.started', { count: 67 }));
  });

  it('regenerates thumbnails without a confirmation and announces the count', async () => {
    const fetchMock = stubFetch({ ok: true, json: () => Promise.resolve({ thumbnail_count: 12 }) });
    window.PHOTO_ORGANIZER.confirmDialog = vi.fn();
    const api = await init();

    await api.startRegenerateThumbnails();

    expect(fetchMock).toHaveBeenCalledWith(REGEN_URL, { method: 'POST' });
    expect(window.PHOTO_ORGANIZER.confirmDialog).not.toHaveBeenCalled();
    expect(window.notification.success).toHaveBeenCalledWith(
      window.testI18n.t('utilities:indexPhotos.missingThumbnails.started', { count: 12 }));
  });

  it.each([
    ['startRetryFailed', 'retry-failed-button', 'retryFailed'],
    ['startRegenerateThumbnails', 'regenerate-thumbnails-button', 'missingThumbnails'],
    ['startIndexNew', 'index-new-button', 'indexNew'],
  ])('%s shows the server error and re-enables its button', async (method, id, keys) => {
    stubFetch({ ok: false, json: () => Promise.resolve({ error: 'Nothing to do' }) });
    const api = await init();

    await api[method]();

    expect(window.notification.failure).toHaveBeenCalledWith('Nothing to do');
    expect(byId(id).disabled).toBe(false);
    expect(byId(id).textContent).toBe(window.testI18n.t(`utilities:indexPhotos.${keys}.button`));
  });
});
