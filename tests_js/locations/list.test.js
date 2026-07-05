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
    },
  };
};

const createI18n = () => ({
  t: (key, options = {}) => {
    if (key === 'locations:unknownLocation') return 'Unknown location';
    if (key === 'locations:selection.photoCount') return `${options.formattedCount} photos`;
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

const initLocationsMap = async ({ reverseLocation = null } = {}) => {
  setupDom();
  installOpenLayersStub();

  const fetchMock = vi.fn((url) => {
    if (url === '/locations/reverse-geocode') {
      return Promise.resolve(window.testHelpers.response({ location_name: reverseLocation }));
    }
    if (url === '/locations/bulk-update') {
      return Promise.resolve(window.testHelpers.response({}));
    }
    return Promise.resolve(window.testHelpers.response({}, { ok: false, status: 404 }));
  });
  vi.stubGlobal('fetch', fetchMock);

  await loadModule('locations/list.js');
  const api = window.PHOTO_ORGANIZER.locations.initMap(createLocations(), createI18n(), createConfig());

  const clusterFeature = new TestFeature({
    features: api.vectorSource.getFeatures(),
    geometry: api.vectorSource.getFeatures()[0].getGeometry(),
  });
  api.selectedFeatures.add(clusterFeature);

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
    expect(document.querySelector('.clusters-summary .cluster-summary-item strong').textContent).toBe('2 photos');
    expect(document.querySelector('.selection-panel-actions .btn-clear-selection')).toBeTruthy();
    expect(document.querySelector('.selection-panel-actions .btn-clear-names')).toBeTruthy();
  });

  it('inserts the reverse-geocode recommendation inside the assignment section', async () => {
    const { api } = await initLocationsMap({ reverseLocation: 'Geo Beach' });

    await api.updateSelectionPanel();
    await flushPromises();

    const assignmentSection = document.querySelector('.selection-assignment');
    const recommended = assignmentSection.querySelector('.btn-recommended');
    const existingQuickActions = assignmentSection.querySelectorAll('.quick-actions')[1];

    expect(recommended).toBeTruthy();
    expect(recommended.textContent.trim()).toBe('Geo Beach');
    const recommendedSection = recommended.closest('.quick-actions');
    expect(
      recommendedSection.compareDocumentPosition(existingQuickActions) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
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
    api.selectedFeatures.clear();

    const cluster = clickCluster(api, api.vectorSource.getFeatures());

    const panel = document.getElementById('selection-panel');
    expect(panel.classList.contains('active')).toBe(true);
    expect(api.selectedFeatures.has(cluster)).toBe(true);
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
    api.selectedFeatures.clear();
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
    api.selectedFeatures.clear();
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
    api.selectedFeatures.clear();

    clickCluster(api, [api.vectorSource.getFeatures()[0]]);

    expect(document.getElementById('preview-photo-select')).toBeFalsy();
    expect(document.querySelectorAll('.preview-thumb')).toHaveLength(0);
    expect(document.getElementById('photo-name').textContent).toBe('one.jpg');
    // assignment tools are still offered for the single cluster
    expect(document.getElementById('mass-assign-btn')).toBeTruthy();
  });

  it('the preview section collapses, and stays collapsed across re-renders', async () => {
    const { api } = await initLocationsMap();
    api.selectedFeatures.clear();
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
    api.selectedFeatures.clear();
    const panel = document.getElementById('selection-panel');

    clickCluster(api, api.vectorSource.getFeatures());
    expect(panel.classList.contains('active')).toBe(true);

    api.map.forEachFeatureAtPixel = () => null;
    api.map.handlers.click({ pixel: [0, 0] });
    expect(api.selectedFeatures.size).toBe(0);
    expect(panel.classList.contains('active')).toBe(false);
  });

  it('a plain click replaces an existing box selection with the clicked cluster', async () => {
    const { api } = await initLocationsMap(); // helper seeds a box selection
    await api.updateSelectionPanel();
    expect(document.querySelector('.selection-panel-header h3').textContent).toBe('Mass assignment');

    const cluster = clickCluster(api, [api.vectorSource.getFeatures()[0]]);

    expect(api.selectedFeatures.size).toBe(1);
    expect(api.selectedFeatures.has(cluster)).toBe(true);
    // the panel stays in its single unified layout
    expect(document.querySelector('.preview-section')).toBeTruthy();
    expect(document.getElementById('mass-assign-btn')).toBeTruthy();
  });

  it('assigning a name from a clicked cluster posts its photo ids', async () => {
    const { api, fetchMock } = await initLocationsMap();
    api.selectedFeatures.clear();
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
