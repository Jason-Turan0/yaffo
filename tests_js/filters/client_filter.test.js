import { loadModule } from '../support/load_module.js';

// The client-side filter engine mirrors the home route's server-side filter
// semantics (yaffo/routes/home.py); these tests pin the shared rules: empty
// controls mean "no filter", any/all people-and-label matching, tag name/value
// matching, and the same flat-earth proximity bounding box.

const FORM_HTML = `
  <form id="filter-form">
    <input type="text" name="path" value="">
    <select name="year"><option value="" selected></option><option value="2021">2021</option></select>
    <select name="month"><option value="" selected></option><option value="7">7</option></select>
    <select name="device"><option value="" selected></option><option value="X-T4">X-T4</option></select>
    <select name="favorite"><option value="" selected></option><option value="1">1</option></select>
    <select name="media-type"><option value="" selected></option><option value="video">video</option></select>
    <select name="shape"><option value="" selected></option><option value="portrait">portrait</option><option value="landscape">landscape</option></select>
    <select name="gender"><option value="" selected></option><option value="0">0</option><option value="1">1</option></select>
    <input type="radio" name="person-match-type" value="any" checked>
    <input type="radio" name="person-match-type" value="all">
    <input type="checkbox" name="person" value="1">
    <input type="checkbox" name="person" value="2">
    <input type="radio" name="labels-match-type" value="any" checked>
    <input type="radio" name="labels-match-type" value="all">
    <input type="checkbox" name="labels" value="10">
    <input type="checkbox" name="labels" value="20">
    <select name="tag-name"><option value="" selected></option><option value="Event">Event</option></select>
    <select name="tag-value"><option value="" selected></option><option value="Vacation">Vacation</option></select>
    <input type="checkbox" name="location" value="Old Town">
    <input type="checkbox" name="unnamed" value="1">
    <input type="hidden" name="proximity-lat" value="">
    <input type="hidden" name="proximity-lon" value="">
    <input type="number" name="proximity-distance" value="">
  </form>
`;

const baseItem = () => ({
  id: 1,
  name: 'Old Town',
  photo_path: '/media/2021/IMG_1.jpg',
  media_type: 'photo',
  shape: 'landscape',
  lat: 43.4,
  lon: 11.8,
  year: 2021,
  month: 7,
  device: 'X-T4',
  favorite: true,
  person_ids: [1, 2],
  genders: [0, 1],
  label_ids: [10, 20],
  tags: [{ name: 'Event', value: 'Vacation' }],
});

let core;
let form;

const setControl = (name, value) => {
  const control = form.querySelector(`[name="${name}"]`);
  control.value = value;
};

const check = (name, value) => {
  form.querySelector(`[name="${name}"][value="${value}"]`).checked = true;
};

const predicate = () => core.buildPredicate(core.readCriteria(form), { distanceUnit: 'km' });

beforeEach(async () => {
  document.body.innerHTML = FORM_HTML;
  form = document.getElementById('filter-form');
  const app = await loadModule('filters/client_filter.js');
  core = app.filters.clientFilterCore;
});

describe('readCriteria', () => {
  it('treats an untouched form as no filters', () => {
    const criteria = core.readCriteria(form);
    expect(criteria).toMatchObject({
      path: null, year: null, month: null, device: null, favorite: false,
      mediaType: null, shape: null, gender: null, tagName: null, tagValue: null,
      unnamed: false, proximity: null,
    });
    expect(criteria.personIds).toEqual([]);
    expect(criteria.labelIds).toEqual([]);
    expect(criteria.locationNames).toEqual([]);
  });

  it('collects multi-select values and match types', () => {
    check('person', '1');
    check('person', '2');
    form.querySelector('[name="person-match-type"][value="all"]').checked = true;
    const criteria = core.readCriteria(form);
    expect(criteria.personIds).toEqual([1, 2]);
    expect(criteria.personMatchType).toBe('all');
  });
});

describe('buildPredicate', () => {
  it('matches everything when nothing is selected', () => {
    expect(predicate()(baseItem())).toBe(true);
    expect(predicate()({ id: 2 })).toBe(true);
  });

  it('path filter is a case-insensitive substring over the stored path', () => {
    setControl('path', 'img_1');
    expect(predicate()(baseItem())).toBe(true);
    expect(predicate()({ ...baseItem(), photo_path: '/media/other.png' })).toBe(false);
  });

  it('filters on year, month, device, favorite and media type', () => {
    setControl('year', '2021');
    setControl('month', '7');
    setControl('device', 'X-T4');
    setControl('favorite', '1');
    expect(predicate()(baseItem())).toBe(true);
    expect(predicate()({ ...baseItem(), year: 2020 })).toBe(false);
    expect(predicate()({ ...baseItem(), favorite: false })).toBe(false);

    setControl('media-type', 'video');
    expect(predicate()(baseItem())).toBe(false);
    expect(predicate()({ ...baseItem(), media_type: 'video' })).toBe(true);
  });

  it('people: any matches one overlap, all requires every selected id', () => {
    check('person', '1');
    check('person', '2');
    expect(predicate()({ ...baseItem(), person_ids: [2, 9] })).toBe(true);

    check('person-match-type', 'all');
    expect(predicate()({ ...baseItem(), person_ids: [2, 9] })).toBe(false);
    expect(predicate()({ ...baseItem(), person_ids: [1, 2, 9] })).toBe(true);
  });

  it('gender matches when any face resolves to the selected gender', () => {
    setControl('gender', '0');
    expect(predicate()(baseItem())).toBe(true);
    expect(predicate()({ ...baseItem(), genders: [1] })).toBe(false);
    expect(predicate()({ ...baseItem(), genders: [] })).toBe(false);
  });

  it('labels follow the same any/all rules as people', () => {
    check('labels', '10');
    expect(predicate()({ ...baseItem(), label_ids: [10] })).toBe(true);
    expect(predicate()({ ...baseItem(), label_ids: [30] })).toBe(false);
  });

  it('tag name alone matches any value; name+value must both match', () => {
    setControl('tag-name', 'Event');
    expect(predicate()(baseItem())).toBe(true);
    expect(predicate()({ ...baseItem(), tags: [{ name: 'Place', value: 'x' }] })).toBe(false);

    setControl('tag-value', 'Vacation');
    expect(predicate()(baseItem())).toBe(true);
    expect(predicate()({ ...baseItem(), tags: [{ name: 'Event', value: 'Work' }] })).toBe(false);
  });

  it('location names match the marker name', () => {
    check('location', 'Old Town');
    expect(predicate()(baseItem())).toBe(true);
    expect(predicate()({ ...baseItem(), name: 'Beach' })).toBe(false);
    expect(predicate()({ ...baseItem(), name: null })).toBe(false);
  });

  it('unnamed-only keeps items whose name is null or empty', () => {
    check('unnamed', '1');
    expect(predicate()(baseItem())).toBe(false);
    expect(predicate()({ ...baseItem(), name: null })).toBe(true);
    expect(predicate()({ ...baseItem(), name: '' })).toBe(true);
  });

  it('proximity uses the server bounding box in the saved unit', () => {
    setControl('proximity-lat', '38.6');
    setControl('proximity-lon', '-90.2');
    setControl('proximity-distance', '1'); // 1 km ≈ 0.009° latitude
    const near = { ...baseItem(), lat: 38.6005, lon: -90.2 };
    const far = { ...baseItem(), lat: 38.62, lon: -90.2 };
    expect(predicate()(near)).toBe(true);
    expect(predicate()(far)).toBe(false);

    // the same distance in miles reaches farther
    const miles = core.buildPredicate(core.readCriteria(form), { distanceUnit: 'mi' });
    expect(miles({ ...baseItem(), lat: 38.614, lon: -90.2 })).toBe(true);
  });
});

describe('initClientFilter', () => {
  it('intercepts submit and hands the map a fresh predicate', async () => {
    const app = await loadModule('filters/client_filter.js');
    const received = [];
    app.filters.initClientFilter({
      form,
      distanceUnit: 'km',
      onApply: (p) => received.push(p),
    });

    setControl('year', '2021');
    form.dispatchEvent(new window.Event('submit', { cancelable: true }));

    expect(received).toHaveLength(1);
    expect(received[0]({ ...baseItem(), year: 2021 })).toBe(true);
    expect(received[0]({ ...baseItem(), year: 1999 })).toBe(false);
  });
});


describe('shape', () => {
  it('keeps only items of the selected shape', () => {
    setControl('shape', 'portrait');
    const matches = predicate();

    expect(matches({ ...baseItem(), shape: 'portrait' })).toBe(true);
    expect(matches({ ...baseItem(), shape: 'landscape' })).toBe(false);
  });

  it('drops an item whose dimensions were never recorded', () => {
    // Mirrors the SQL, where the NULL width/height comparison is false: an unsized
    // item has no known shape, so it belongs to none of them.
    setControl('shape', 'portrait');

    expect(predicate()({ ...baseItem(), shape: null })).toBe(false);
  });

  it('ignores an unknown shape rather than matching nothing', () => {
    setControl('shape', 'bogus');

    expect(core.readCriteria(form).shape).toBe(null);
    expect(predicate()({ ...baseItem(), shape: 'landscape' })).toBe(true);
  });
});
