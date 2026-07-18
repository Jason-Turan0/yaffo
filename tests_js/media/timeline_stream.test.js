import { loadModule } from '../support/load_module.js';

const loadStream = async () => (await loadModule('media/timeline_stream.js')).media.timelineStream;

const section = (date, { continuation = false, cards = 1, header = true } = {}) => `
  <section class="timeline-section${continuation ? ' is-continuation' : ''}" data-date="${date}">
    ${header ? `<h3 class="timeline-day-header">
      <span class="timeline-day-label">${date}</span>
      <span class="timeline-day-count">${cards} items</span>
    </h3>` : ''}
    <div class="photo-grid">${'<div class="photo-card"></div>'.repeat(cards)}</div>
  </section>`;

describe('mergeContinuations', () => {
  it('folds a continuation into the same-day section above and updates its count', async () => {
    document.body.innerHTML = `<div class="timeline">
      ${section('2025-07-03', { cards: 2 })}
      ${section('2025-07-03', { continuation: true, cards: 3, header: false })}
      ${section('2024-06-15', { cards: 1 })}
    </div>`;
    const stream = await loadStream();
    const timeline = document.querySelector('.timeline');

    stream.mergeContinuations(timeline, window.testI18n);

    const sections = timeline.querySelectorAll('.timeline-section');
    expect(sections.length).toBe(2);
    expect(sections[0].querySelectorAll('.photo-card').length).toBe(5);
    // testI18n.t echoes the key: proves the count label was re-rendered.
    expect(sections[0].querySelector('.timeline-day-count').textContent).toBe('media:timeline.itemCount');
  });

  it('keeps an orphaned continuation rather than dropping its photos', async () => {
    document.body.innerHTML = `<div class="timeline">
      ${section('2024-06-15', { cards: 1 })}
      ${section('2025-07-03', { continuation: true, cards: 2, header: false })}
    </div>`;
    const stream = await loadStream();
    const timeline = document.querySelector('.timeline');

    stream.mergeContinuations(timeline, window.testI18n);

    const sections = timeline.querySelectorAll('.timeline-section');
    expect(sections.length).toBe(2);
    expect(sections[1].classList.contains('is-continuation')).toBe(false);
    expect(sections[1].querySelectorAll('.photo-card').length).toBe(2);
  });
});

describe('init', () => {
  it('merges and rewires after every htmx swap', async () => {
    document.body.innerHTML = `<div class="timeline">${section('2025-07-03', { cards: 1 })}</div>`;
    const stream = await loadStream();
    const favoriteInit = vi.fn();
    window.PHOTO_ORGANIZER.media.favorite = { init: favoriteInit };

    stream.init(window.testI18n, { i18n: { locale: 'en' } });
    document.querySelector('.timeline').insertAdjacentHTML(
      'beforeend', section('2025-07-03', { continuation: true, cards: 2, header: false }));
    document.body.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));

    expect(document.querySelectorAll('.timeline-section').length).toBe(1);
    expect(document.querySelectorAll('.photo-card').length).toBe(3);
    expect(favoriteInit).toHaveBeenCalled();
  });

  it('does nothing on pages without a timeline', async () => {
    document.body.innerHTML = '<div class="photo-grid"></div>';
    const stream = await loadStream();

    expect(() => stream.init(window.testI18n, { i18n: { locale: 'en' } })).not.toThrow();
  });
});
