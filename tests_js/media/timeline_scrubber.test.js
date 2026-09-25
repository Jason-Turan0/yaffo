import { loadModule } from '../support/load_module.js';

const loadScrubber = async () => (await loadModule('media/timeline_scrubber.js')).media.timelineScrubber;

// July 2025 (30 items), June 2024 (1 item) — newest first, cumulative offsets.
// The 10 empty months between are short enough to stay to scale, so the rail is
// 14 equal month slices; top/height are what the server computes for that.
const SLICE = 100 / 14;
const months = [
  { year: 2025, month: 7, count: 30, offset: 0, top: 0, height: SLICE },
  { year: 2024, month: 6, count: 1, offset: 30, top: 13 * SLICE, height: SLICE },
];

// The same library plus one photo dated January 5000. The ~2,975 empty years
// collapse to a 3-month break: units 0 (Jan 5000), 1-3 (break), 4 (Jul 2025),
// 5-16 (empty, to scale), 17 (Jun 2024) — 18 units in all.
const UNIT = 100 / 18;
const withOutlier = [
  { year: 5000, month: 1, count: 1, offset: 0, top: 0, height: UNIT },
  { year: 2025, month: 7, count: 30, offset: 1, top: 4 * UNIT, height: UNIT },
  { year: 2024, month: 6, count: 1, offset: 31, top: 17 * UNIT, height: UNIT },
];

const config = { i18n: { locale: 'en' } };

const fixture = () => {
  document.body.innerHTML = `
    <nav class="navbar"></nav>
    <div class="timeline">
      <section class="timeline-section" data-date="2025-07-20"></section>
      <section class="timeline-section" data-date="2024-06-15"></section>
      <section class="timeline-section" data-date="unknown"></section>
    </div>
    <nav id="timeline-scrubber" data-page-size="25">
      <a class="timeline-scrubber-year" data-year="2025" href="/?view=timeline&page=1">2025</a>
      <a class="timeline-scrubber-year" data-year="2024" href="/?view=timeline&page=2">2024</a>
      <span class="timeline-scrubber-marker"></span>
      <div class="timeline-scrubber-bubble" hidden></div>
    </nav>
    <script type="application/json" id="timeline-index">${JSON.stringify(months)}</script>`;
};

describe('monthAtFraction', () => {
  it('maps a fraction of the time axis to a month, snapping empty months older', async () => {
    const scrubber = await loadScrubber();

    // July 2025 → June 2024 spans 14 calendar months.
    expect(scrubber.monthAtFraction(months, 0)).toEqual(months[0]);
    // 0.05 * 14 is still inside July 2025's slice.
    expect(scrubber.monthAtFraction(months, 0.05)).toEqual(months[0]);
    // Mid-rail lands in an empty month (Dec 2024) and snaps older to June 2024.
    expect(scrubber.monthAtFraction(months, 0.5)).toEqual(months[1]);
    expect(scrubber.monthAtFraction(months, 0.99)).toEqual(months[1]);
  });

  it('clamps out-of-range fractions', async () => {
    const scrubber = await loadScrubber();

    expect(scrubber.monthAtFraction(months, -1)).toEqual(months[0]);
    expect(scrubber.monthAtFraction(months, 2)).toEqual(months[1]);
  });

  it('reads server positions, so a collapsed break snaps to the photos below it', async () => {
    const scrubber = await loadScrubber();

    expect(scrubber.monthAtFraction(withOutlier, 0.02)).toEqual(withOutlier[0]);
    // Inside the break (units 1-3): the next older month with photos.
    expect(scrubber.monthAtFraction(withOutlier, 0.1)).toEqual(withOutlier[1]);
    // The real library keeps most of the rail: Jul 2025 → Jun 2024 spans 4/18 → 1.
    expect(scrubber.monthAtFraction(withOutlier, 0.24)).toEqual(withOutlier[1]);
    expect(scrubber.monthAtFraction(withOutlier, 0.6)).toEqual(withOutlier[2]);
  });
});

describe('jumpUrl', () => {
  it('targets the month page and its divider anchor, keeping current filters', async () => {
    const scrubber = await loadScrubber();

    const url = scrubber.jumpUrl(months[1], 25, 'http://localhost/?year=2024&view=timeline');

    expect(url).toBe('http://localhost/?year=2024&view=timeline&page=2#month-2024-06');
  });

  it('zero-pads the anchor month', async () => {
    const scrubber = await loadScrubber();

    const url = scrubber.jumpUrl({ year: 2025, month: 7, count: 30, offset: 0 }, 25, 'http://localhost/');

    expect(url).toContain('#month-2025-07');
  });
});

describe('railPercentForDate', () => {
  it('maps dates onto the same calendar-month axis and clamps its ends', async () => {
    const scrubber = await loadScrubber();

    expect(scrubber.railPercentForDate(months, '2025-07-31')).toBe(0);
    expect(scrubber.railPercentForDate(months, '2024-12-31')).toBeCloseTo(50);
    expect(scrubber.railPercentForDate(months, '2023-01-01')).toBe(100);
    expect(scrubber.railPercentForDate(months, 'unknown')).toBeNull();
  });

  it('places dates on the collapsed axis the server laid out', async () => {
    const scrubber = await loadScrubber();

    expect(scrubber.railPercentForDate(withOutlier, '5000-01-31')).toBe(0);
    // Jul 2025 starts below the break, not a sliver from the bottom.
    expect(scrubber.railPercentForDate(withOutlier, '2025-07-31')).toBeCloseTo(4 * UNIT);
    // An empty month in the to-scale stretch: Dec 2024 is 6 months below Jul 2025's band.
    expect(scrubber.railPercentForDate(withOutlier, '2024-12-31')).toBeCloseTo(11 * UNIT);
    expect(scrubber.railPercentForDate(withOutlier, '2024-06-30')).toBeCloseTo(17 * UNIT);
  });
});

describe('pageForMonth', () => {
  it('computes the 1-based page from the cumulative offset', async () => {
    const scrubber = await loadScrubber();

    expect(scrubber.pageForMonth(months[0], 25)).toBe(1);
    expect(scrubber.pageForMonth(months[1], 25)).toBe(2);
    expect(scrubber.pageForMonth({ ...months[1], offset: 25 }, 25)).toBe(2);
    expect(scrubber.pageForMonth({ ...months[1], offset: 24 }, 25)).toBe(1);
  });
});

describe('init', () => {
  beforeEach(() => {
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      callback(0);
      return 1;
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it('shows a locale month bubble on pointer movement over the rail', async () => {
    fixture();
    const scrubber = await loadScrubber();
    scrubber.init(window.testI18n, config);
    const rail = document.getElementById('timeline-scrubber');
    const bubble = rail.querySelector('.timeline-scrubber-bubble');
    rail.getBoundingClientRect = () => ({ top: 0, height: 100 });

    // 2% down a 14-month rail is still inside the newest month's slice.
    rail.dispatchEvent(new MouseEvent('pointermove', { clientY: 2, bubbles: true }));

    expect(bubble.hidden).toBe(false);
    expect(bubble.textContent).toBe('July 2025');

    rail.dispatchEvent(new MouseEvent('pointerleave', { bubbles: true }));

    expect(bubble.hidden).toBe(true);
  });

  it('does nothing without the timeline payload', async () => {
    document.body.innerHTML = '<nav id="timeline-scrubber"></nav>';
    const scrubber = await loadScrubber();

    expect(() => scrubber.init(window.testI18n, config)).not.toThrow();
  });

  it('publishes the measured navbar height for the sticky day headers', async () => {
    document.body.innerHTML = '<nav class="navbar"></nav>';
    const navbar = document.querySelector('.navbar');
    Object.defineProperty(navbar, 'offsetHeight', { value: 64 });
    const scrubber = await loadScrubber();

    scrubber.init(window.testI18n, config);

    expect(document.documentElement.style.getPropertyValue('--navbar-height')).toBe('64px');
  });

  it('tracks the topmost viewport date and highlights its year', async () => {
    fixture();
    const [newest, older] = document.querySelectorAll('.timeline-section');
    newest.getBoundingClientRect = () => ({ bottom: -1 });
    older.getBoundingClientRect = () => ({ bottom: 200 });
    const scrubber = await loadScrubber();

    scrubber.init(window.testI18n, config);

    expect(document.querySelector('.timeline-scrubber-marker').style.top).toBe('96.42857142857143%');
    expect(document.querySelector('[data-year="2024"]').classList.contains('is-active')).toBe(true);
    expect(document.querySelector('[data-year="2025"]').classList.contains('is-active')).toBe(false);
  });

  it('pins the marker to the bottom for the undated tail after a streamed swap', async () => {
    fixture();
    const sections = document.querySelectorAll('.timeline-section');
    sections.forEach((section) => { section.getBoundingClientRect = () => ({ bottom: -1 }); });
    const scrubber = await loadScrubber();
    scrubber.init(window.testI18n, config);

    document.body.dispatchEvent(new Event('htmx:afterSwap'));

    expect(document.querySelector('.timeline-scrubber-marker').style.top).toBe('100%');
    expect(document.querySelector('.timeline-scrubber-year.is-active')).toBeNull();
  });

  it('briefly shows the visible month while scrolling, then hides it', async () => {
    fixture();
    const [newest] = document.querySelectorAll('.timeline-section');
    newest.getBoundingClientRect = () => ({ bottom: 200 });
    const scrubber = await loadScrubber();
    scrubber.init(window.testI18n, config);
    const bubble = document.querySelector('.timeline-scrubber-bubble');

    expect(bubble.hidden).toBe(true);
    window.dispatchEvent(new Event('scroll'));

    expect(bubble.hidden).toBe(false);
    expect(bubble.textContent).toBe('July 2025');
    expect(bubble.classList.contains('is-transient')).toBe(true);

    bubble.dispatchEvent(new Event('animationend'));

    expect(bubble.hidden).toBe(true);
    expect(bubble.classList.contains('is-transient')).toBe(false);
  });
});
