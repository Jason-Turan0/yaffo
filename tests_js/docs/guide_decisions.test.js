const chooser = (suffix = '') => `<div class="action-chooser">
<div class="action-choice" data-choice="New photos"><h3 id="new${suffix}">Index</h3><p>Keep faces.</p><a href="#reference">Read more</a></div>
<div class="action-choice" data-choice="Metadata"><h3 id="metadata${suffix}">Reindex</h3><p>Rebuild faces.</p></div></div>`;
const navigator = `<div class="troubleshooting-navigator">
<div class="navigator-step"><h3 id="folder">Folder available?</h3><ul><li><a href="#index">Yes</a></li><li><a href="#drive">No</a></li></ul></div>
<div class="navigator-step"><h3 id="index">Waiting?</h3><a href="#drive">No</a></div>
<div class="navigator-step"><h3 id="drive">Check drive</h3><a href="drive/">Full instructions</a></div></div>`;
let initialize;
const load = async () => {
  vi.resetModules();
  vi.stubGlobal('document$', {subscribe: (callback) => { initialize = callback; callback(); }});
  await import('../../docs/javascripts/guide-decisions.js');
};
beforeEach(() => { window.history.replaceState({}, '', '/'); });
afterEach(() => { document.body.replaceChildren(); initialize?.(); vi.unstubAllGlobals(); });
it('selects an action with keyboard focus and preserves its reference links', async () => {
  document.body.innerHTML = chooser();
  await load();
  const buttons = document.querySelectorAll('button');
  buttons[0].dispatchEvent(new KeyboardEvent('keydown', {key: 'End', bubbles: true}));
  expect(document.activeElement).toBe(buttons[1]);
  expect(buttons[1].getAttribute('aria-pressed')).toBe('true');
  expect(document.querySelectorAll('.action-choice:not([hidden])')).toHaveLength(1);
  expect(document.querySelector('.action-choice').hidden).toBe(true);
  expect(document.querySelector('a[href="#reference"]')).not.toBeNull();
});
it('follows branches, goes back, resets, and moves focus to the new question', async () => {
  document.body.innerHTML = navigator;
  await load();
  const byText = (text) => [...document.querySelectorAll('button')].find((b) => b.textContent === text);
  expect(byText('← Back one question').disabled).toBe(true);
  byText('Yes').click();
  expect(document.activeElement.id).toBe('index');
  expect(document.querySelector('.guide-decisions__status').textContent).toContain('Choose an answer');
  document.querySelector('#index').parentElement.querySelector('button').click();
  expect(document.activeElement.id).toBe('drive');
  expect(document.querySelector('a[href="drive/"]')).not.toBeNull();
  byText('← Back one question').click();
  expect(document.activeElement.id).toBe('index');
  byText('Start over').click();
  expect(document.activeElement.id).toBe('folder');
  expect(byText('Start over').disabled).toBe(true);
});
it('leaves malformed authoring fully readable', async () => {
  document.body.innerHTML = navigator.replace('href="#index"', 'href="#unknown"');
  const before = document.body.innerHTML;
  await load();
  expect(document.body.innerHTML).toBe(before);
});
it('supports heading links, independent instances, and repeated instant navigation', async () => {
  document.body.innerHTML = chooser() + chooser('-two') + navigator;
  window.history.replaceState({}, '', '#metadata');
  await load(); initialize();
  expect(document.querySelectorAll('.action-chooser button')).toHaveLength(4);
  expect(document.getElementById('metadata').parentElement.hidden).toBe(false);
  expect(document.getElementById('new-two').parentElement.hidden).toBe(false);
  window.history.replaceState({}, '', '#drive');
  window.dispatchEvent(new HashChangeEvent('hashchange'));
  expect(document.getElementById('drive').parentElement.hidden).toBe(false);
  document.body.innerHTML = navigator;
  initialize();
  expect(document.querySelectorAll('.guide-decisions__controls')).toHaveLength(1);
});

it('recognizes same-page answer URLs expanded by Material instant navigation', async () => {
  document.body.innerHTML = navigator.replace('href="#index"', `href="${window.location.origin}/#index"`);
  await load();
  const answer = [...document.querySelectorAll('button')].find((b) => b.textContent === 'Yes');
  expect(answer).toBeDefined();
  answer.click();
  expect(document.getElementById('index').parentElement.hidden).toBe(false);
});

it('opens the choice containing a linked subsection', async () => {
  document.body.innerHTML = chooser().replace('<p>Rebuild faces.</p>', '<h4 id="reindex-details">Full details</h4><p>Rebuild faces.</p>');
  window.history.replaceState({}, '', '#reindex-details');
  await load();
  expect(document.getElementById('metadata').parentElement.hidden).toBe(false);
  window.history.replaceState({}, '', '#new');
  window.dispatchEvent(new HashChangeEvent('hashchange'));
  expect(document.getElementById('new').parentElement.hidden).toBe(false);
});

it('uses outcome paragraphs as navigator step headings', async () => {
  document.body.innerHTML = navigator.replace('<h3 id="drive">Check drive</h3>', '<p id="drive" class="navigator-outcome"><strong>Check drive</strong></p>');
  await load();
  document.querySelector('#index').parentElement.querySelector('button').click();
  expect(document.activeElement.id).toBe('drive');
  expect(document.getElementById('drive').parentElement.hidden).toBe(false);
  expect(document.querySelector('.guide-decisions__status').textContent).toContain('Next steps');
});

it('keeps same-page links to sections outside the navigator as links', async () => {
  document.body.innerHTML = navigator.replace('<a href="drive/">', '<a href="#jobs">Jobs</a><a href="drive/">') + '<h2 id="jobs">Jobs</h2>';
  await load();
  expect(document.querySelector('.troubleshooting-navigator').dataset.enhanced).toBe('true');
  expect(document.querySelector('a[href="#jobs"]')).not.toBeNull();
});

it('starts with every choice hidden and closes the open choice when clicked again', async () => {
  document.body.innerHTML = chooser().replace('class="action-chooser"', 'class="action-chooser" data-initial="none"');
  await load();
  const buttons = document.querySelectorAll('button');
  expect(document.querySelectorAll('.action-choice:not([hidden])')).toHaveLength(0);
  expect(buttons[0].getAttribute('aria-pressed')).toBe('false');
  buttons[1].click();
  expect(document.getElementById('metadata').parentElement.hidden).toBe(false);
  buttons[1].click();
  expect(document.querySelectorAll('.action-choice:not([hidden])')).toHaveLength(0);
  expect(document.querySelector('.guide-decisions__status').textContent).toBe('Choose an option above');
});

it('accepts H4 navigator questions nested inside a choice', async () => {
  document.body.innerHTML = navigator.replaceAll('<h3', '<h4').replaceAll('</h3>', '</h4>');
  await load();
  expect(document.querySelector('.troubleshooting-navigator').dataset.enhanced).toBe('true');
});

it('uses a per-chooser status template', async () => {
  document.body.innerHTML = chooser().replace('class="action-chooser"', 'class="action-chooser" data-status="Checks for: {choice}"');
  await load();
  document.querySelectorAll('button')[1].click();
  expect(document.querySelector('.guide-decisions__status').textContent).toBe('Checks for: Metadata');
});

it('shows no status line for a chooser without a status template', async () => {
  document.body.innerHTML = chooser();
  await load();
  document.querySelectorAll('button')[1].click();
  expect(document.querySelector('.guide-decisions__status').hidden).toBe(true);
});
