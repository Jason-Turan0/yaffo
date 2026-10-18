const fixture = (suffix = '') => `
  <div class="annotated-screenshot">
    <p><a class="glightbox" href="photo.webp"><img src="photo.webp" alt="Photo details"></a></p>
    <div class="screenshot-annotation" data-x="25" data-y="12" data-region="2 9 23 26">
      <h3 id="information${suffix}">File information<a class="headerlink" href="#information${suffix}">¶</a></h3>
      <p>Check the original file. <a href="#reference${suffix}">Read more</a></p>
    </div>
    <div class="screenshot-annotation" data-x="95" data-y="4" data-region="92 1.8 5.5 5.5">
      <h3 id="favorite${suffix}">Favorite</h3>
      <p>Mark a favorite.</p>
    </div>
  </div>`;

let initialize;
const load = async () => {
  vi.resetModules();
  vi.stubGlobal('document$', {subscribe: (callback) => {
    initialize = callback;
    callback();
  }});
  await import('../../docs/javascripts/annotated-screenshot.js');
};

beforeEach(() => {
  document.body.innerHTML = fixture();
  window.history.replaceState({}, '', '/');
});

afterEach(() => {
  document.body.replaceChildren();
  initialize?.();
  vi.unstubAllGlobals();
});

describe('annotated guide screenshot', () => {
  it('synchronizes marker, instructions, and keyboard focus without replacing the lightbox image', async () => {
    const image = document.querySelector('img');
    const lightbox = document.querySelector('.glightbox');
    await load();
    const markers = document.querySelectorAll('.annotated-screenshot__marker');
    const controls = document.querySelectorAll('.annotated-screenshot__select');
    expect(document.querySelector('img')).toBe(image);
    expect(document.querySelector('.glightbox')).toBe(lightbox);
    markers[1].click();
    expect(controls[1].getAttribute('aria-expanded')).toBe('true');
    expect(document.getElementById('information-explanation').hidden).toBe(true);
    expect(document.getElementById('favorite-explanation').hidden).toBe(false);
    markers[1].dispatchEvent(new KeyboardEvent('keydown', {key: 'Home', bubbles: true}));
    expect(document.activeElement).toBe(markers[0]);
    expect(markers[0].getAttribute('aria-pressed')).toBe('true');
    expect(controls[0].textContent).not.toContain('¶');
    expect(document.querySelector('a[href="#reference"]')).not.toBeNull();
  });

  it.each(['2 9 110 26', '2 9 23', '2 9 NaN 26'])('keeps the static content readable for invalid region %s', async (region) => {
    document.querySelector('.screenshot-annotation').dataset.region = region;
    const before = document.body.innerHTML;
    await load();
    expect(document.body.innerHTML).toBe(before);
    expect(document.querySelector('[hidden]')).toBeNull();
  });

  it('enhances independently and only once across instant page navigation', async () => {
    document.body.innerHTML = fixture() + fixture('-second');
    await load();
    initialize();
    expect(document.querySelectorAll('.annotated-screenshot__marker')).toHaveLength(4);
    document.querySelector('.annotated-screenshot__marker').click();
    expect(document.querySelectorAll('.annotated-screenshot__marker[aria-pressed="true"]')).toHaveLength(2);
    document.body.innerHTML = fixture('-new-page');
    initialize();
    expect(document.querySelectorAll('.annotated-screenshot__marker')).toHaveLength(2);
    expect(document.getElementById('information-new-page-explanation').hidden).toBe(false);
  });

  it('opens the explanation addressed by a heading link, initially and after a hash change', async () => {
    window.history.replaceState({}, '', '#favorite');
    await load();
    expect(document.getElementById('favorite-explanation').hidden).toBe(false);
    window.history.replaceState({}, '', '#information');
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    expect(document.getElementById('information-explanation').hidden).toBe(false);
    expect(document.getElementById('favorite-explanation').hidden).toBe(true);
  });

  it('accepts H4 annotation headings for screenshots inside H3 sections', async () => {
    document.body.innerHTML = fixture().replaceAll('<h3', '<h4').replaceAll('</h3>', '</h4>');
    await load();
    expect(document.querySelectorAll('.annotated-screenshot__marker')).toHaveLength(2);
    expect(document.getElementById('information-explanation').hidden).toBe(false);
  });
});
