import { loadModule } from '../support/load_module.js';

class TestGeometry {
  constructor(coordinates) {
    this.coordinates = coordinates;
  }

  getCoordinates() {
    return this.coordinates;
  }

  getExtent() {
    const [x, y] = this.coordinates;
    return [x, y, x, y];
  }
}

class TestFeature {
  constructor(properties) {
    this.properties = { ...properties };
  }

  get(name) {
    return this.properties[name];
  }

  set(name, value) {
    this.properties[name] = value;
  }

  getGeometry() {
    return this.properties.geometry;
  }
}

class TestVectorSource {
  constructor({ features = [] } = {}) {
    this.features = [...features];
  }

  getFeatures() {
    return this.features;
  }

  clear() {
    this.features = [];
  }

  addFeatures(features) {
    this.features.push(...features);
  }

  getExtent() {
    return [0, 0, 10, 10];
  }
}

class TestView {
  constructor(options) {
    this.options = options;
    this.fit = vi.fn();
  }
}

class TestMap {
  constructor(options) {
    this.options = options;
    this.view = options.view;
    this.handlers = {};
    this.addLayer = vi.fn();
    this.addInteraction = vi.fn();
    this.addOverlay = vi.fn();
  }

  getView() {
    return this.view;
  }

  getTargetElement() {
    return document.getElementById(this.options.target);
  }

  forEachFeatureAtPixel() {
    return null;
  }

  hasFeatureAtPixel() {
    return false;
  }

  on(eventName, handler) {
    this.handlers[eventName] = handler;
  }

  getSize() {
    return [800, 600];
  }
}

class TestDragBox {
  constructor() {
    this.handlers = {};
  }

  on(eventName, handler) {
    this.handlers[eventName] = handler;
  }

  getGeometry() {
    return { getExtent: () => [0, 0, 10, 10] };
  }
}

const setupDom = () => {
  document.body.innerHTML = `
    <div id="map"></div>
    <div id="selection-panel" class="selection-panel">
      <div id="selection-panel-content"></div>
    </div>
  `;
};

const installOpenLayersStub = () => {
  window.ol = {
    Map: TestMap,
    View: TestView,
    Feature: TestFeature,
    geom: {
      Point: TestGeometry,
    },
    layer: {
      Tile: class {},
      Vector: class {
        constructor(options) {
          this.options = options;
          this.changed = vi.fn();
        }
      },
    },
    source: {
      OSM: class {},
      Vector: TestVectorSource,
      Cluster: class {
        constructor({ source }) {
          this.source = source;
        }

        getFeatures() {
          return this.source.getFeatures().map((feature) => new TestFeature({
            features: [feature],
            geometry: feature.getGeometry(),
          }));
        }
      },
    },
    interaction: {
      DragBox: TestDragBox,
    },
    events: {
      condition: {
        shiftKeyOnly: vi.fn(),
      },
    },
    extent: {
      intersects: () => true,
    },
    proj: {
      fromLonLat: ([lon, lat]) => [lon, lat],
      toLonLat: ([lon, lat]) => [lon, lat],
    },
    style: {
      Style: class {},
      Circle: class {},
      Fill: class {},
      Stroke: class {},
      Text: class {},
      Icon: class {},
    },
  };
};

const createI18n = () => ({
  t: (key, options = {}) => {
    if (key === 'locations:unknownLocation') return 'Unknown location';
    if (key === 'locations:selection.photoCount') return `${options.formattedCount} photos`;
    if (key === 'locations:selection.photos') return 'photos';
    if (key === 'locations:selection.summaryOtherOne') return `${options.photos} photos in ${options.clusters} cluster`;
    if (key === 'locations:selection.summaryOtherOther') return `${options.photos} photos in ${options.clusters} clusters`;
    if (key === 'locations:selection.massAssignment') return 'Mass assignment';
    if (key === 'locations:selection.quickAssign') return 'Quick assign';
    if (key === 'locations:selection.recommended') return 'Recommended';
    if (key === 'locations:selection.customLocation') return 'Custom location';
    if (key === 'locations:selection.assignAll') return 'Assign all';
    if (key === 'locations:selection.selectedClusters') return 'Selected clusters';
    if (key === 'locations:selection.clearNames') return 'Clear location names';
    if (key === 'locations:selection.clearSelection') return 'Clear selection';
    if (key === 'locations:update.succeeded') return `Assigned ${options.count} to ${options.location}`;
    if (key === 'locations:update.cleared') return `Cleared ${options.count}`;
    if (key === 'locations:update.nameRequired') return 'Name required';
    if (key === 'locations:update.failed') return 'Update failed';
    if (key === 'locations:popup.selectPhoto') return `Select photo (${options.total} total):`;
    if (key === 'locations:selection.preview') return 'Preview';
    if (key === 'locations:selection.moreThumbnails') return `+${options.more} more`;
    if (key === 'common:close') return 'Close';
    return key;
  },
  number: (value) => String(value),
});

const createConfig = () => ({
  urls: {
    placeholder: '/placeholder.jpg',
    reverse_geocode_route: '/locations/reverse-geocode',
    locations_bulk_update: '/locations/bulk-update',
  },
  buildUrl: (endpoint, params) => `/${endpoint}/${Object.values(params).join('/')}`,
});

const createLocations = () => [
  {
    id: 1,
    name: 'Beach',
    filename: 'one.jpg',
    photo_path: '/one.jpg',
    media_type: 'photo',
    lat: 10,
    lon: 20,
  },
  {
    id: 2,
    name: null,
    filename: 'two.jpg',
    photo_path: '/two.jpg',
    media_type: 'photo',
    lat: 11,
    lon: 21,
  },
];

const flushPromises = () => new Promise((resolve) => setTimeout(resolve, 0));

const initLocationsMap = async ({
  reverseLocation = null,
  reverseResponse = null,
  locations = createLocations(),
  nearbyRadiusKm = 10,
} = {}) => {
  setupDom();
  installOpenLayersStub();

  const fetchMock = vi.fn((url) => {
    if (url === '/locations/reverse-geocode') {
      if (reverseResponse) return reverseResponse;
      return Promise.resolve(window.testHelpers.response({ location_name: reverseLocation }));
    }
    if (url === '/locations/bulk-update') {
      return Promise.resolve(window.testHelpers.response({}));
    }
    return Promise.resolve(window.testHelpers.response({}, { ok: false, status: 404 }));
  });
  vi.stubGlobal('fetch', fetchMock);

  await loadModule('locations/list.js');
  const api = window.PHOTO_ORGANIZER.locations.initMap(
    locations,
    createI18n(),
    createConfig(),
    { nearbyRadiusKm },
  );

  const clusterFeature = new TestFeature({
    features: api.vectorSource.getFeatures(),
    geometry: api.vectorSource.getFeatures()[0].getGeometry(),
  });
  clusterFeature.get('features').forEach((feature) => api.selectedPhotoIds.add(feature.get('id')));

  return { api, fetchMock };
};

describe('locations list map selection panel', () => {
  it('renders the relaid-out mass assignment controls and selected cluster summary', async () => {
    const { api } = await initLocationsMap();

    await api.updateSelectionPanel();

    const panel = document.getElementById('selection-panel');
    expect(panel.classList.contains('active')).toBe(true);
    expect(document.querySelector('.selection-panel-header h3').textContent).toBe('Mass assignment');
    expect(document.querySelector('.selection-assignment #mass-location-input')).toBeTruthy();
    expect(document.querySelector('.selection-assignment #mass-assign-btn')).toBeTruthy();
    expect(Array.from(document.querySelectorAll('.clusters-summary .cluster-summary-item strong')).map((item) => item.textContent)).toEqual([
      '1 photos',
      '1 photos',
    ]);
    expect(document.querySelector('.selection-panel-actions .btn-clear-selection')).toBeTruthy();
    expect(document.querySelector('.selection-panel-actions .btn-clear-names')).toBeTruthy();
  });

  it('inserts the reverse-geocode recommendation inside the assignment section', async () => {
    const { api } = await initLocationsMap({
      reverseLocation: 'Geo Beach',
      locations: [
        { id: 1, name: null, filename: 'one.jpg', photo_path: '/one.jpg', media_type: 'photo', lat: 10, lon: 20 },
        { id: 2, name: null, filename: 'two.jpg', photo_path: '/two.jpg', media_type: 'photo', lat: 11, lon: 21 },
      ],
    });

    await api.updateSelectionPanel();
    await flushPromises();

    const assignmentSection = document.querySelector('.selection-assignment');
    const recommended = assignmentSection.querySelector('.btn-recommended');
    const clusterAssign = assignmentSection.querySelector('.cluster-assign');

    expect(recommended).toBeTruthy();
    expect(recommended.textContent.trim()).toBe('Geo Beach');
    const recommendedSection = recommended.closest('.quick-actions');
    expect(
      recommendedSection.compareDocumentPosition(clusterAssign) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('uses the only nearby location name around the selected medoid as the recommendation', async () => {
    const { api, fetchMock } = await initLocationsMap({
      reverseLocation: 'Geo Beach',
      nearbyRadiusKm: 2,
      locations: [
        { id: 1, name: null, filename: 'one.jpg', photo_path: '/one.jpg', media_type: 'photo', lat: 0, lon: 0 },
        { id: 2, name: 'Beach', filename: 'two.jpg', photo_path: '/two.jpg', media_type: 'photo', lat: 0, lon: 0.01 },
        { id: 3, name: null, filename: 'three.jpg', photo_path: '/three.jpg', media_type: 'photo', lat: 10, lon: 10 },
      ],
    });
    api.selectedPhotoIds.clear();
    api.selectedPhotoIds.add(1);

    await api.updateSelectionPanel();
    await flushPromises();

    expect(document.querySelector('.btn-recommended').textContent.trim()).toBe('Beach');
    expect(fetchMock).not.toHaveBeenCalledWith('/locations/reverse-geocode', expect.anything());
  });

  it('reverse-geocodes the selected-photo medoid when nearby names are ambiguous', async () => {
    const { api, fetchMock } = await initLocationsMap({
      reverseLocation: 'Geo Beach',
      nearbyRadiusKm: 200,
      locations: [
        { id: 1, name: null, filename: 'one.jpg', photo_path: '/one.jpg', media_type: 'photo', lat: 0, lon: 0 },
        { id: 2, name: null, filename: 'two.jpg', photo_path: '/two.jpg', media_type: 'photo', lat: 0, lon: 1 },
        { id: 3, name: null, filename: 'three.jpg', photo_path: '/three.jpg', media_type: 'photo', lat: 0, lon: 100 },
        { id: 4, name: 'Beach', filename: 'four.jpg', photo_path: '/four.jpg', media_type: 'photo', lat: 0, lon: 1.01 },
        { id: 5, name: 'Park', filename: 'five.jpg', photo_path: '/five.jpg', media_type: 'photo', lat: 0, lon: 1.02 },
      ],
    });
    api.selectedPhotoIds.clear();
    api.selectedPhotoIds.add(1);
    api.selectedPhotoIds.add(2);
    api.selectedPhotoIds.add(3);

    await api.updateSelectionPanel();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith('/locations/reverse-geocode', expect.objectContaining({
      body: JSON.stringify({ lat: 0, lon: 1 }),
    }));
  });

  it('deduplicates pending recommendation lookups and renders one recommended button', async () => {
    let resolveReverse;
    const reverseResponse = new Promise((resolve) => {
      resolveReverse = resolve;
    });
    const { api, fetchMock } = await initLocationsMap({
      reverseResponse,
      locations: [
        { id: 1, name: null, filename: 'one.jpg', photo_path: '/one.jpg', media_type: 'photo', lat: 10, lon: 20 },
        { id: 2, name: null, filename: 'two.jpg', photo_path: '/two.jpg', media_type: 'photo', lat: 11, lon: 21 },
      ],
    });

    await api.updateSelectionPanel();
    await api.updateSelectionPanel();
    resolveReverse(window.testHelpers.response({ location_name: 'Geo Beach' }));
    await flushPromises();
    await flushPromises();

    expect(fetchMock.mock.calls.filter(([url]) => url === '/locations/reverse-geocode')).toHaveLength(1);
    expect(document.querySelectorAll('.btn-recommended')).toHaveLength(1);
    expect(document.querySelector('.btn-recommended').textContent.trim()).toBe('Geo Beach');
  });

  it('stops reverse-geocode fallback lookups after the provider rate limit is hit', async () => {
    const { api, fetchMock } = await initLocationsMap({
      reverseResponse: Promise.resolve(window.testHelpers.response({
        error: 'Rate limited',
        code: 'reverse_geocode_rate_limited',
      }, { ok: false, status: 429 })),
      locations: [
        { id: 1, name: null, filename: 'one.jpg', photo_path: '/one.jpg', media_type: 'photo', lat: 10, lon: 20 },
        { id: 2, name: null, filename: 'two.jpg', photo_path: '/two.jpg', media_type: 'photo', lat: 20, lon: 30 },
      ],
    });

    api.selectedPhotoIds.clear();
    api.selectedPhotoIds.add(1);
    await api.updateSelectionPanel();
    await flushPromises();
    expect(fetchMock.mock.calls.filter(([url]) => url === '/locations/reverse-geocode')).toHaveLength(1);
    expect(document.querySelector('.btn-recommended')).toBeFalsy();

    api.selectedPhotoIds.clear();
    api.selectedPhotoIds.add(2);
    await api.updateSelectionPanel();
    await flushPromises();

    expect(fetchMock.mock.calls.filter(([url]) => url === '/locations/reverse-geocode')).toHaveLength(1);
    expect(document.querySelector('.btn-recommended')).toBeFalsy();
  });

  it('assigns the typed location to selected photos', async () => {
    const { api, fetchMock } = await initLocationsMap();

    await api.updateSelectionPanel();
    document.getElementById('mass-location-input').value = 'New Harbor';
    document.getElementById('mass-assign-btn').click();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith('/locations/bulk-update', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        media_item_ids: [1, 2],
        location_name: 'New Harbor',
      }),
    }));
    expect(api.vectorSource.getFeatures().every((feature) => feature.get('name') === 'New Harbor')).toBe(true);
    expect(window.notification.success).toHaveBeenCalledWith('Assigned 2 to New Harbor');
    expect(document.getElementById('selection-panel').classList.contains('active')).toBe(false);
  });

  it('clears selected location names with an explicit clear payload', async () => {
    const { api, fetchMock } = await initLocationsMap();

    await api.updateSelectionPanel();
    document.querySelector('.btn-clear-names').click();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith('/locations/bulk-update', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        media_item_ids: [1, 2],
        clear: true,
      }),
    }));
    expect(api.vectorSource.getFeatures().every((feature) => feature.get('name') === null)).toBe(true);
    expect(window.notification.success).toHaveBeenCalledWith('Cleared 2');
  });
});

describe('locations list map client-side filtering', () => {
  it('setClientFilter narrows the markers by the sidebar predicate', async () => {
    const { api } = await initLocationsMap();

    api.setClientFilter((item) => item.id === 1);

    expect(api.vectorSource.getFeatures().map((feature) => feature.get('id'))).toEqual([1]);
  });

  it('an unnamed-only predicate narrows to markers without a name and restores on match-all', async () => {
    const { api } = await initLocationsMap();

    api.setClientFilter((item) => !item.name); // only the unnamed marker (id 2)
    expect(api.vectorSource.getFeatures().map((feature) => feature.get('id'))).toEqual([2]);

    api.setClientFilter(() => true);
    expect(api.vectorSource.getFeatures()).toHaveLength(2);
  });

  it('name filters see names assigned after page load', async () => {
    const { api } = await initLocationsMap();

    // assign flows update the feature's filterable payload in step
    await api.updateSelectionPanel();
    document.getElementById('mass-location-input').value = 'New Harbor';
    document.getElementById('mass-assign-btn').click();
    await flushPromises();

    api.setClientFilter((item) => item.name === 'New Harbor');
    expect(api.vectorSource.getFeatures()).toHaveLength(2);

    api.setClientFilter((item) => item.name === 'Beach');
    expect(api.vectorSource.getFeatures()).toEqual([]);
  });
});

describe('locations list map cluster preview', () => {
  const clickCluster = (api, features) => {
    const cluster = new TestFeature({ features });
    api.map.forEachFeatureAtPixel = () => cluster;
    api.map.handlers.click({ pixel: [0, 0] });
    return cluster;
  };

  it('clicking a cluster selects it and shows preview plus assignment tools', async () => {
    const { api } = await initLocationsMap();
    api.selectedPhotoIds.clear();

    const cluster = clickCluster(api, api.vectorSource.getFeatures());

    const panel = document.getElementById('selection-panel');
    expect(panel.classList.contains('active')).toBe(true);
    expect(cluster.get('features').every((feature) => api.selectedPhotoIds.has(feature.get('id')))).toBe(true);
    // one panel for both tasks: preview section + assignment controls
    expect(document.querySelector('.preview-toggle span').textContent).toBe('Preview');
    expect(document.getElementById('mass-assign-btn')).toBeTruthy();
    // preview hosts the image, the select box and the thumbnails
    expect(document.getElementById('photo-img').getAttribute('src')).toBe('/media/1');
    expect(document.getElementById('preview-photo-select')).toBeTruthy();
    expect(document.querySelectorAll('.preview-thumb')).toHaveLength(2);
    expect(document.getElementById('photo-name').textContent).toBe('one.jpg');
    expect(document.querySelector('.photo-location').textContent).toBe('Beach');
  });

  it('the select box swaps the previewed image, link and caption', async () => {
    const { api } = await initLocationsMap();
    api.selectedPhotoIds.clear();
    clickCluster(api, api.vectorSource.getFeatures());

    const select = document.getElementById('preview-photo-select');
    select.value = '1';
    select.dispatchEvent(new window.Event('change'));

    expect(document.getElementById('photo-img').getAttribute('src')).toBe('/media/2');
    expect(document.getElementById('photo-name').textContent).toBe('two.jpg');
    expect(document.querySelector('.photo-location').textContent).toBe('Unknown location');
  });

  it('clicking a thumbnail selects that photo and highlights it', async () => {
    const { api } = await initLocationsMap();
    api.selectedPhotoIds.clear();
    clickCluster(api, api.vectorSource.getFeatures());

    const thumbs = document.querySelectorAll('.preview-thumb');
    thumbs[1].click();

    expect(document.getElementById('photo-img').getAttribute('src')).toBe('/media/2');
    expect(document.getElementById('preview-photo-select').value).toBe('1');
    expect(thumbs[1].classList.contains('active')).toBe(true);
    expect(thumbs[0].classList.contains('active')).toBe(false);
  });

  it('a single-photo cluster previews without the select box or thumbnails', async () => {
    const { api } = await initLocationsMap();
    api.selectedPhotoIds.clear();

    clickCluster(api, [api.vectorSource.getFeatures()[0]]);

    expect(document.getElementById('preview-photo-select')).toBeFalsy();
    expect(document.querySelectorAll('.preview-thumb')).toHaveLength(0);
    expect(document.getElementById('photo-name').textContent).toBe('one.jpg');
    // assignment tools are still offered for the single cluster
    expect(document.getElementById('mass-assign-btn')).toBeTruthy();
  });

  it('the preview section collapses, and stays collapsed across re-renders', async () => {
    const { api } = await initLocationsMap();
    api.selectedPhotoIds.clear();
    clickCluster(api, api.vectorSource.getFeatures());

    const toggle = document.querySelector('.preview-toggle');
    toggle.click();
    expect(document.querySelector('.preview-section').classList.contains('collapsed')).toBe(true);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    // a new selection re-renders the panel; the collapsed state sticks
    clickCluster(api, [api.vectorSource.getFeatures()[0]]);
    expect(document.querySelector('.preview-section').classList.contains('collapsed')).toBe(true);
  });

  it('clicking empty map clears the selection and closes the panel', async () => {
    const { api } = await initLocationsMap();
    api.selectedPhotoIds.clear();
    const panel = document.getElementById('selection-panel');

    clickCluster(api, api.vectorSource.getFeatures());
    expect(panel.classList.contains('active')).toBe(true);

    api.map.forEachFeatureAtPixel = () => null;
    api.map.handlers.click({ pixel: [0, 0] });
    expect(api.selectedPhotoIds.size).toBe(0);
    expect(panel.classList.contains('active')).toBe(false);
  });

  it('a plain click replaces an existing selection with an unselected clicked cluster', async () => {
    const { api } = await initLocationsMap(); // helper seeds a box selection
    await api.updateSelectionPanel();
    expect(document.querySelector('.selection-panel-header h3').textContent).toBe('Mass assignment');

    api.selectedPhotoIds.clear();
    api.selectedPhotoIds.add(2);
    const cluster = clickCluster(api, [api.vectorSource.getFeatures()[0]]);

    expect(api.selectedPhotoIds.size).toBe(1);
    expect(cluster.get('features').every((feature) => api.selectedPhotoIds.has(feature.get('id')))).toBe(true);
    // the panel stays in its single unified layout
    expect(document.querySelector('.preview-section')).toBeTruthy();
    expect(document.getElementById('mass-assign-btn')).toBeTruthy();
  });

  it('clicking a partially selected cluster makes it fully selected, then unselected', async () => {
    const { api } = await initLocationsMap();
    api.selectedPhotoIds.clear();
    const features = api.vectorSource.getFeatures();
    const cluster = new TestFeature({ features });
    api.map.addLayer.mock.calls[0][0].options.source.getFeatures = () => [cluster];
    api.selectedPhotoIds.add(features[0].get('id'));
    api.map.forEachFeatureAtPixel = () => cluster;

    api.map.handlers.click({ pixel: [0, 0] });
    expect(api.selectedPhotoIds).toEqual(new Set([1, 2]));
    expect(document.querySelector('.clusters-summary .cluster-summary-item strong').textContent).toBe('2 photos');

    api.map.handlers.click({ pixel: [0, 0] });
    expect(api.selectedPhotoIds.size).toBe(0);
    expect(document.getElementById('selection-panel').classList.contains('active')).toBe(false);
  });

  it('shift-click adds clusters to the selection and toggles them back out', async () => {
    const { api } = await initLocationsMap();
    api.selectedPhotoIds.clear();
    const [featureA, featureB] = api.vectorSource.getFeatures();

    const clusterA = clickCluster(api, [featureA]);

    const clusterB = new TestFeature({ features: [featureB] });
    api.map.forEachFeatureAtPixel = () => clusterB;
    api.map.handlers.click({ pixel: [5, 5], originalEvent: { shiftKey: true } });

    expect(api.selectedPhotoIds.size).toBe(2);
    expect(clusterA.get('features').every((feature) => api.selectedPhotoIds.has(feature.get('id')))).toBe(true);
    expect(clusterB.get('features').every((feature) => api.selectedPhotoIds.has(feature.get('id')))).toBe(true);
    // the combined selection previews both photos
    expect(document.querySelectorAll('.preview-thumb')).toHaveLength(2);

    // shift-clicking a selected cluster removes it again
    api.map.handlers.click({ pixel: [5, 5], originalEvent: { shiftKey: true } });
    expect(api.selectedPhotoIds.size).toBe(1);
    expect(clusterA.get('features').every((feature) => api.selectedPhotoIds.has(feature.get('id')))).toBe(true);
  });

  it('a missed shift-click on empty map keeps the selection', async () => {
    const { api } = await initLocationsMap();
    api.selectedPhotoIds.clear();
    clickCluster(api, [api.vectorSource.getFeatures()[0]]);

    api.map.forEachFeatureAtPixel = () => null;
    api.map.handlers.click({ pixel: [0, 0], originalEvent: { shiftKey: true } });

    expect(api.selectedPhotoIds.size).toBe(1);
    expect(document.getElementById('selection-panel').classList.contains('active')).toBe(true);
  });

  it('a stationary shift-click does not run the drag-box selection', async () => {
    const { api } = await initLocationsMap();
    api.selectedPhotoIds.clear();
    const clusterA = clickCluster(api, [api.vectorSource.getFeatures()[0]]);

    const dragBox = api.map.addInteraction.mock.calls[0][0];
    dragBox.handlers.boxstart({ mapBrowserEvent: { pixel: [10, 10] } });
    dragBox.handlers.boxend({ mapBrowserEvent: { pixel: [12, 11] } });

    // below the drag threshold the box is ignored; the click handler owns it
    expect(api.selectedPhotoIds.size).toBe(1);
    expect(clusterA.get('features').every((feature) => api.selectedPhotoIds.has(feature.get('id')))).toBe(true);
  });

  it('a real shift-drag adds the box contents to the current selection', async () => {
    const { api } = await initLocationsMap();
    api.selectedPhotoIds.clear();
    const clusterA = clickCluster(api, [api.vectorSource.getFeatures()[0]]);

    const dragBox = api.map.addInteraction.mock.calls[0][0];
    dragBox.handlers.boxstart({ mapBrowserEvent: { pixel: [10, 10] } });
    dragBox.handlers.boxend({ mapBrowserEvent: { pixel: [80, 90] } });

    // the earlier click selection survives; the stubbed cluster source yields
    // one cluster per marker, all inside the box
    expect(clusterA.get('features').every((feature) => api.selectedPhotoIds.has(feature.get('id')))).toBe(true);
    expect(api.selectedPhotoIds.size).toBe(2);
  });

  it('the panel × clears the selection and closes the panel', async () => {
    const { api } = await initLocationsMap();
    api.selectedPhotoIds.clear();
    clickCluster(api, api.vectorSource.getFeatures());
    const panel = document.getElementById('selection-panel');
    expect(panel.classList.contains('active')).toBe(true);

    document.querySelector('.selection-panel-close').click();

    expect(api.selectedPhotoIds.size).toBe(0);
    expect(panel.classList.contains('active')).toBe(false);
  });

  it('assigning a name from a clicked cluster posts its photo ids', async () => {
    const { api, fetchMock } = await initLocationsMap();
    api.selectedPhotoIds.clear();
    clickCluster(api, api.vectorSource.getFeatures());

    document.getElementById('mass-location-input').value = 'Click Bay';
    document.getElementById('mass-assign-btn').click();
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith('/locations/bulk-update', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        media_item_ids: [1, 2],
        location_name: 'Click Bay',
      }),
    }));
  });
});
