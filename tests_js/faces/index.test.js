import { loadModule } from '../support/load_module.js';

// faces.initAssignment drives the face-assignment page: it paints a sampled grid
// per cluster, tracks a whole-cluster selection (every id selected by default,
// clicking a visible face toggles it), and POSTs the selection to /faces/assign —
// advancing to the next cluster on success. These tests build a minimal cluster
// fixture and drive the delegated handlers plus the returned public API.

const config = () => ({
  urls: {
    placeholder: '/static/placeholder.png',
    api_faces_update: '/api/faces',
    faces_person_create: '/faces/people/create',
    face_shortcut_people_settings: '/settings/faces/shortcuts',
  },
  buildUrl: (endpoint, params = {}) => {
    let url = `/${endpoint}`;
    for (const [k, v] of Object.entries(params)) url += `/${k}/${v}`;
    return url;
  },
});

const group = (faces, { people = [] } = {}) => `
  <div class="suggestion-group" data-faces='${JSON.stringify(faces)}'>
    <button class="chip chip-action cluster-select-all"
            data-select-all-label="Select all ${faces.length} faces"
            data-select-none-label="Clear selection"></button>
    <button class="shuffle-sample-btn"></button>
    <span class="cluster-page-label"></span>
    <span class="sample-range"></span>
    <button class="cluster-first"></button>
    <button class="cluster-prev"></button>
    <button class="cluster-next"></button>
    <button class="cluster-last"></button>
    <div class="grid"></div>
    ${people.map((p) => `<button class="assign-group-btn" data-person-id="${p.id}" data-person-name="${p.name}"></button>`).join('')}
  </div>`;

const shortcutModal = (people) => `
  <button id="configure-shortcuts-btn"></button>
  <div id="shortcutPeopleModal">
    <form data-js-form>
      <div id="shortcut-config-list">
        ${people.map((p) => `
          <div class="shortcut-config-row" data-person-id="${p.id}">
            <span class="filter-config-handle"></span>
            <input type="checkbox" class="shortcut-config-toggle" ${p.checked ? 'checked' : ''}>
            <span>${p.name}</span>
            <div class="shortcut-config-move">
              <button type="button" class="shortcut-config-move-btn" data-move="up"></button>
              <button type="button" class="shortcut-config-move-btn" data-move="down"></button>
            </div>
          </div>`).join('')}
      </div>
      <button type="button" id="shortcut-config-reset"></button>
    </form>
  </div>`;

const fixture = (groupsHtml, { shortcutPeople = [] } = {}) => {
  document.body.innerHTML = `
    <div id="clusters">${groupsHtml}</div>
    <div id="sidebar-shortcut-people"></div>
    <button id="sidebar-ignore-btn"></button>
    ${shortcutPeople.length ? shortcutModal(shortcutPeople) : ''}`;
  // initAssignment opens the keyboard-help modal via the shared component.
  window.PHOTO_ORGANIZER.COMPONENTS.modal = {
    init: (id) => ({
      formElement: document.querySelector(`#${id} form`),
      open: vi.fn(),
      close: vi.fn(),
    }),
  };
};

const stubFetch = (body, { ok = true } = {}) => {
  const fetchMock = vi.fn(() => Promise.resolve({ ok, json: () => Promise.resolve(body) }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
};

const init = async (sampleSize = 10, shortcutPeople = [], allPeople = shortcutPeople, customized = false) =>
  (await loadModule('faces/index.js')).faces.initAssignment(
    sampleSize,
    shortcutPeople,
    allPeople,
    customized,
    window.testI18n,
    config(),
  );

const setPreviewMedia = ({ phone, precise }) => {
  vi.spyOn(window, 'matchMedia').mockImplementation((query) => ({
    matches: query === '(max-width: 640px)' ? phone : precise,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
};

const activeFaces = () =>
  Array.from(document.querySelectorAll('.suggestion-group:not([hidden]) .face'));

// initAssignment attaches a document-level `keydown` listener. jsdom's document
// persists across tests in a file, so without cleanup those listeners accumulate
// and stale ones fire on later dispatches. Track and remove them per test.
const docListeners = [];
beforeEach(() => {
  docListeners.length = 0;
  const original = document.addEventListener.bind(document);
  vi.spyOn(document, 'addEventListener').mockImplementation((type, handler, opts) => {
    docListeners.push([type, handler, opts]);
    original(type, handler, opts);
  });
});
afterEach(() => {
  for (const [type, handler, opts] of docListeners) document.removeEventListener(type, handler, opts);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('faces initAssignment — rendering & selection', () => {
  it('paints the first cluster with every face selected', async () => {
    fixture(group([{ id: 1 }, { id: 2 }, { id: 3 }]));
    const api = await init();

    expect(activeFaces().length).toBe(3);
    expect(activeFaces().every((el) => el.classList.contains('selected'))).toBe(true);
    expect([...api.getSelectedIds()].sort()).toEqual([1, 2, 3]);
    // sample-range reads "1–3" via the i18n number formatter (identity in tests).
    expect(document.querySelector('.sample-range').textContent).toBe('1–3');
  });

  it('marks ignored faces (the "Include ignored faces" filter) and only those', async () => {
    fixture(group([{ id: 1 }, { id: 2, ignored: true }, { id: 3, ignored: false }]));
    await init();

    const ignored = activeFaces().filter((el) => el.classList.contains('face-ignored'));
    expect(ignored.map((el) => el.dataset.faceId)).toEqual(['2']);
    expect(document.querySelectorAll('.face-ignored-badge').length).toBe(1);
    expect(ignored[0].querySelector('.face-ignored-badge')).not.toBeNull();
  });

  it('toggles a single face off when clicked', async () => {
    fixture(group([{ id: 1 }, { id: 2 }, { id: 3 }]));
    const api = await init();

    document.querySelector('.face[data-face-id="2"]').click();

    expect(api.getSelectedIds().has(2)).toBe(false);
    expect(document.querySelector('.face[data-face-id="2"]').classList.contains('selected')).toBe(false);
    expect([...api.getSelectedIds()].sort()).toEqual([1, 3]);
  });

  it('clears the whole cluster when the select-all chip is pressed', async () => {
    fixture(group([{ id: 1 }, { id: 2 }]));
    const api = await init();

    // A cluster arrives fully selected, so the chip offers to CLEAR it...
    const chip = document.querySelector('.cluster-select-all');
    expect(chip.textContent).toBe('Clear selection');

    chip.click();

    expect(api.getSelectedIds().size).toBe(0);
    expect(activeFaces().some((el) => el.classList.contains('selected'))).toBe(false);
    // ...and now offers to select them all again — the label always names the next action.
    expect(chip.textContent).toBe('Select all 2 faces');
  });

  it('re-selects the whole cluster when the chip is pressed again', async () => {
    fixture(group([{ id: 1 }, { id: 2 }]));
    const api = await init();
    const chip = document.querySelector('.cluster-select-all');

    chip.click();  // clear
    chip.click();  // select all

    expect([...api.getSelectedIds()].sort()).toEqual([1, 2]);
    expect(chip.textContent).toBe('Clear selection');
  });

  it('flips the chip back to "select all" when a single face is unticked', async () => {
    fixture(group([{ id: 1 }, { id: 2 }]));
    const api = await init();
    const chip = document.querySelector('.cluster-select-all');

    document.querySelector('.face[data-face-id="2"]').click();

    expect(api.getSelectedIds().has(2)).toBe(false);
    expect(chip.textContent).toBe('Select all 2 faces');
  });
});

describe('faces initAssignment — submitFaces', () => {
  it('never sends an assignment without a person (that would clear the faces)', async () => {
    fixture(group([{ id: 1 }]));
    const fetchMock = stubFetch({ faces: [] });
    const api = await init();

    await api.submitFaces(null, 'ASSIGNED');

    expect(fetchMock).not.toHaveBeenCalled();
    expect(window.notification.error).toHaveBeenCalled();
  });

  it('PATCHes the selection and advances to the next cluster on success', async () => {
    fixture(group([{ id: 1 }, { id: 2 }]) + group([{ id: 3 }]));
    const fetchMock = stubFetch({ faces: [], message: 'ok' });
    const api = await init();

    await api.submitFaces('7', 'ASSIGNED');

    expect(fetchMock).toHaveBeenCalledWith('/api/faces', expect.objectContaining({ method: 'PATCH' }));
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({ ids: [1, 2], person_id: 7 });

    // The second cluster is now the visible one.
    const visible = document.querySelector('.suggestion-group:not([hidden])');
    expect(visible.querySelector('.face').dataset.faceId).toBe('3');
  });

  it('skips (info + advance) without fetching when nothing is selected', async () => {
    fixture(group([{ id: 1 }]) + group([{ id: 2 }]));
    const fetchMock = stubFetch({ faces: [] });
    const api = await init();

    api.selectWholeCluster(false);
    await api.submitFaces('7', 'ASSIGNED');

    expect(fetchMock).not.toHaveBeenCalled();
    expect(window.notification.info).toHaveBeenCalled();
    expect(document.querySelector('.suggestion-group:not([hidden]) .face').dataset.faceId).toBe('2');
  });
});

describe('faces initAssignment — phone source preview modal', () => {
  beforeEach(() => setPreviewMedia({ phone: true, precise: false }));

  const openPreview = (faceId) => {
    document
      .querySelector(`.face[data-face-id="${faceId}"] .face-preview-button`)
      .click();
    return document.querySelector('.face-preview-modal').textContent;
  };

  // jsdom never fetches the image, so stand in for the decode: report the natural
  // size the region outline is scaled against, then fire `load`.
  const loadPreview = (naturalWidth, naturalHeight) => {
    const image = document.querySelector('.face-preview-modal .face-source-image');
    Object.defineProperty(image, 'naturalWidth', { value: naturalWidth });
    Object.defineProperty(image, 'naturalHeight', { value: naturalHeight });
    image.dispatchEvent(new window.Event('load'));
    return image;
  };

  it('previews the photo the face was cropped from', async () => {
    fixture(group([{ id: 1, media_item_id: 42, media_type: 'photo', photo_date: '2020-01-01' }]));
    await init();
    openPreview(1);

    expect(document.querySelector('.face-preview-modal .face-source-image').getAttribute('src'))
      .toBe('/media/media_item_id/42');
    expect(document.querySelector('.face-preview-modal').classList.contains('active')).toBe(true);
    expect(document.querySelector('.face-preview-modal').getAttribute('role')).toBe('dialog');
    expect(document.querySelector('.face-tooltip').classList.contains('visible')).toBe(false);
  });

  it('outlines the detection box over the letterboxed photo', async () => {
    fixture(group([{
      id: 1,
      media_item_id: 42,
      media_type: 'photo',
      region: { top: 100, right: 300, bottom: 300, left: 100 },
    }]));
    await init();
    openPreview(1);
    // An 800×400 photo `contain`-fitted into the 320px square scales by 0.4 and is
    // letterboxed with an 80px band top and bottom. The 200px box is padded by 10%
    // of itself (20px a side) before scaling.
    loadPreview(800, 400);

    const box = document.querySelector('.face-preview-modal .face-source-region');
    expect(box.hidden).toBe(false);
    expect(box.style.left).toBe('32px');          // (100 - 20) * 0.4, no horizontal band
    expect(box.style.top).toBe('112px');          // 80 band + (100 - 20) * 0.4
    expect(box.style.width).toBe('96px');         // (320 - 80) * 0.4
    expect(box.style.height).toBe('96px');
  });

  it('clamps the padded box to the photo for a face at the edge of the frame', async () => {
    fixture(group([{
      id: 1,
      media_item_id: 42,
      media_type: 'photo',
      region: { top: 0, right: 100, bottom: 100, left: 0 },  // flush to the top-left
    }]));
    await init();
    openPreview(1);
    loadPreview(400, 400);  // square photo: scales by 0.8, no letterboxing

    const box = document.querySelector('.face-preview-modal .face-source-region');
    expect(box.style.left).toBe('0px');           // padding would go negative; clamped
    expect(box.style.top).toBe('0px');
    expect(box.style.width).toBe('88px');         // (110 - 0) * 0.8 — padded on one side only
    expect(box.style.height).toBe('88px');
  });

  it('draws no outline for a face indexed without a detection box', async () => {
    fixture(group([{ id: 1, media_item_id: 42, media_type: 'photo', region: null }]));
    await init();
    openPreview(1);
    loadPreview(800, 400);

    expect(document.querySelector('.face-preview-modal .face-source-region')).toBe(null);
  });

  it('notes that a video-sourced face has no preview instead of showing an image', async () => {
    fixture(group([{ id: 1, media_item_id: 42, media_type: 'video', photo_date: '2020-01-01' }]));
    await init();
    const text = openPreview(1);

    expect(document.querySelector('.face-preview-modal .face-source-image')).toBe(null);
    expect(text).toContain('faces:assignment.videoPreviewUnavailable');
  });

  it('shows the similarity line for a face that has a score', async () => {
    fixture(group([{ id: 1, similarity: 0.87, photo_date: '2020-01-01' }]));
    await init();

    expect(openPreview(1)).toContain('similarityValue');
  });

  it('lists every person a face matched, not only the most likely', async () => {
    fixture(group([{
      id: 1, similarity: 0.62, photo_date: '2020-01-01',
      matches: [
        { person_id: 7, name: 'Nathan', similarity: 0.62 },
        { person_id: 3, name: 'Chase', similarity: 0.55 },
      ],
    }]));
    const t = vi.spyOn(window.testI18n, 't');
    await init();

    const text = openPreview(1);
    // testI18n renders the key; both people appear, best first, instead of one line.
    expect(text).not.toContain('similarityValue');
    expect(text.match(/faces:assignment\.personSimilarity/g)).toHaveLength(2);
    const named = t.mock.calls
      .filter(([key]) => key === 'faces:assignment.personSimilarity')
      .map(([, values]) => values.name);
    expect(named).toEqual(['Nathan', 'Chase']);
  });

  it('keeps the single similarity line when a face matched one person', async () => {
    fixture(group([{
      id: 1, similarity: 0.62, photo_date: '2020-01-01',
      matches: [{ person_id: 7, name: 'Nathan', similarity: 0.62 }],
    }]));
    await init();

    const text = openPreview(1);
    expect(text).toContain('similarityValue');
    expect(text).not.toContain('personSimilarity');
  });

  it('omits the similarity line for a face with no score', async () => {
    // Similarity-grouped clusters carry no per-face score. The empty data-attribute
    // must not read as 0 — that painted every face as "0%".
    fixture(group([{ id: 1, similarity: null, photo_date: '2020-01-01' }]));
    await init();

    expect(openPreview(1)).not.toContain('similarityValue');
    expect(document.querySelector('.face-preview-modal').textContent).toContain('dateValue');
  });

  it('reads "unknown" for a face with no capture date', async () => {
    // The shared test `t` drops its interpolations, so widen it to expose the
    // value the tooltip passes in — that value is what this test is about.
    window.testI18n.t = (key, options = {}) => `${key}=${options.value ?? ''}`;
    fixture(group([{ id: 1, similarity: 0.87, photo_date: null }]));
    await init();

    expect(openPreview(1)).toContain('common:dateValue=common:unknown=');
  });

  it('closes from its close button without changing selection and restores focus', async () => {
    fixture(group([{ id: 1, media_item_id: 42, media_type: 'photo' }]));
    const api = await init();
    const previewButton = document.querySelector('.face-preview-button');
    previewButton.focus();
    previewButton.click();

    expect(document.querySelector('.face-preview-modal-close')).toBe(document.activeElement);
    document.querySelector('.face-preview-modal-close').click();

    expect(document.querySelector('.face-preview-modal').classList.contains('active')).toBe(false);
    expect(previewButton).toBe(document.activeElement);
    expect(api.getSelectedIds().has(1)).toBe(true);
  });
});

describe('faces initAssignment — tablet and desktop source preview popover', () => {
  it('opens the popover from the explicit control on a touch tablet', async () => {
    setPreviewMedia({ phone: false, precise: false });
    fixture(group([{ id: 1, media_item_id: 42, media_type: 'photo' }]));
    const api = await init();

    document.querySelector('.face-preview-button').click();

    expect(document.querySelector('.face-tooltip').classList.contains('visible')).toBe(true);
    expect(document.querySelector('.face-tooltip .face-source-image').getAttribute('src'))
      .toBe('/media/media_item_id/42');
    expect(document.querySelector('.face-preview-modal').classList.contains('active')).toBe(false);
    expect(api.getSelectedIds().has(1)).toBe(true);
  });

  it('opens and closes the popover on desktop hover', async () => {
    setPreviewMedia({ phone: false, precise: true });
    fixture(group([{ id: 1, media_item_id: 42, media_type: 'photo' }]));
    await init();
    const face = document.querySelector('.face');

    face.dispatchEvent(new window.MouseEvent('mouseover', { bubbles: true }));
    expect(document.querySelector('.face-tooltip').classList.contains('visible')).toBe(true);
    expect(document.querySelector('.face-preview-modal').classList.contains('active')).toBe(false);

    face.dispatchEvent(new window.MouseEvent('mouseout', { bubbles: true }));
    expect(document.querySelector('.face-tooltip').classList.contains('visible')).toBe(false);
  });
});

describe('faces initAssignment — helpers & shortcuts', () => {
  it('randomSample returns n items drawn from the input', async () => {
    fixture(group([{ id: 1 }]));
    const api = await init();

    const input = [{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }];
    const sample = api.randomSample(input, 2);

    expect(sample.length).toBe(2);
    expect(sample.every((f) => input.includes(f))).toBe(true);
  });

  it('assigns the ignored status when the "i" key is pressed', async () => {
    fixture(group([{ id: 1 }, { id: 2 }]) + group([{ id: 3 }]));
    const fetchMock = stubFetch({ faces: [], message: 'ok' });
    await init();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'i', bubbles: true }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({ ids: [1, 2], status: 'IGNORED' });
  });

  it('renders keyboard shortcuts from the active cluster people', async () => {
    fixture(group([{ id: 1 }], { people: [{ id: 9, name: 'Alice' }] }));
    await init();

    const shortcut = document.querySelector('#sidebar-shortcut-people .shortcut-item');
    expect(shortcut.dataset.personId).toBe('9');
    expect(shortcut.textContent).toContain('Alice');
  });

  it('uses saved shortcut people instead of active cluster people', async () => {
    fixture(group([{ id: 1 }], { people: [{ id: 9, name: 'Alice' }] }));
    await init(10, [{ id: 4, name: 'Mina' }], [{ id: 4, name: 'Mina' }, { id: 9, name: 'Alice' }], true);

    const shortcut = document.querySelector('#sidebar-shortcut-people .shortcut-item');
    expect(shortcut.dataset.personId).toBe('4');
    expect(shortcut.textContent).toContain('Mina');
  });

  it('saves edited shortcut people and rebuilds the key map', async () => {
    fixture(group([{ id: 1 }]), {
      shortcutPeople: [
        { id: 4, name: 'Mina', checked: true },
        { id: 8, name: 'Zoe', checked: true },
      ],
    });
    const fetchMock = vi.fn(() => Promise.resolve({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    await init(10, [{ id: 4, name: 'Mina' }], [{ id: 4, name: 'Mina' }, { id: 8, name: 'Zoe' }], false);
    document.querySelector('.shortcut-config-row[data-person-id="8"] .shortcut-config-toggle').checked = true;

    document.querySelector('#shortcutPeopleModal form')
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/settings/faces/shortcuts',
      expect.objectContaining({ method: 'POST' }),
    ));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ person_ids: [4, 8] });
    expect([...document.querySelectorAll('#sidebar-shortcut-people .shortcut-item')]
      .map((el) => el.dataset.personId)).toEqual(['4', '8']);
  });

  it('reorders shortcut rows with the explicit move controls and saves the new order', async () => {
    // The move buttons are the touch-safe alternative to dragging the handle:
    // HTML5 drag-and-drop never fires for touch, so drag alone left the order
    // uneditable on a phone.
    fixture(group([{ id: 1 }]), {
      shortcutPeople: [
        { id: 4, name: 'Mina', checked: true },
        { id: 8, name: 'Zoe', checked: true },
      ],
    });
    const fetchMock = vi.fn(() => Promise.resolve({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    await init(
      10,
      [{ id: 4, name: 'Mina' }, { id: 8, name: 'Zoe' }],
      [{ id: 4, name: 'Mina' }, { id: 8, name: 'Zoe' }],
      true,
    );

    const rowIds = () => [...document.querySelectorAll('.shortcut-config-row')]
      .map((row) => row.dataset.personId);
    expect(rowIds()).toEqual(['4', '8']);

    // The ends of the list are disabled rather than silently inert.
    const first = document.querySelector('.shortcut-config-row[data-person-id="4"]');
    const second = document.querySelector('.shortcut-config-row[data-person-id="8"]');
    expect(first.querySelector('[data-move="up"]').disabled).toBe(true);
    expect(second.querySelector('[data-move="down"]').disabled).toBe(true);

    second.querySelector('[data-move="up"]').click();
    expect(rowIds()).toEqual(['8', '4']);

    document.querySelector('#shortcutPeopleModal form')
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ person_ids: [8, 4] });
  });
});

describe('faces initFilters — People filter only for Group by People', () => {
  const filters = (groupBy) => {
    document.body.innerHTML = `
      <input type="radio" name="group_by" value="people" ${groupBy === 'people' ? 'checked' : ''}>
      <input type="radio" name="group_by" value="similarity" ${groupBy === 'similarity' ? 'checked' : ''}>
      <div class="filter-group" id="people-filter-group">
        <input type="checkbox" name="person" value="1" checked>
        <input type="checkbox" name="person" value="2">
      </div>`;
  };
  const group = () => document.getElementById('people-filter-group');
  const people = () => [...document.querySelectorAll('input[name="person"]')];
  const choose = (value) => {
    const radio = document.querySelector(`input[name="group_by"][value="${value}"]`);
    radio.checked = true;
    radio.dispatchEvent(new Event('change'));
  };

  it('hides and disables the People filter while Similarity is chosen', async () => {
    filters('people');
    (await loadModule('faces/index.js')).faces.initFilters();
    expect(group().hidden).toBe(false);

    choose('similarity');
    expect(group().hidden).toBe(true);
    expect(people().every((input) => input.disabled)).toBe(true);

    choose('people');
    expect(group().hidden).toBe(false);
    expect(people().some((input) => input.disabled)).toBe(false);
    expect(people()[0].checked).toBe(true);  // the selection survives the round trip
  });

  it('starts hidden when the page loads grouped by Similarity', async () => {
    filters('similarity');
    (await loadModule('faces/index.js')).faces.initFilters();
    expect(group().hidden).toBe(true);
  });
});
