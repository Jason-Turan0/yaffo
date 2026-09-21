import { loadModule } from './support/load_module.js';

const setupBaseDom = () => {
  document.body.innerHTML = `
    <nav class="navbar">
      <div class="navbar-container">
        <button id="nav-menu-toggle" aria-expanded="false"></button>
        <div id="navbar-primary"><a href="/">Home</a></div>
        <button id="nav-pages-toggle"></button>
      </div>
      <div id="navbar-context-panels" hidden></div>
      <div id="navbar-pages-bar"></div>
    </nav>
    <div class="alert">
      <button class="alert-close"></button>
    </div>
    <div class="percentage-slider">
      <input type="range" value="0.5">
    </div>
    <div class="percentage-slider-display"><span></span></div>
    <button type="button" id="new-automation-button"></button>
    <div id="newAutomationModal" class="modal">
      <button name="cancel" type="button"></button>
      <form></form>
    </div>
  `;
};

describe('app initializer', () => {
  it('waits for app dependencies, initializes global components, and dispatches completion', async () => {
    setupBaseDom();
    await loadModule('utils.js');
    await loadModule('nav.js');
    await loadModule('components/modal.js');
    await loadModule('components/file_browser.js');
    await loadModule('components/intl_date_input.js');
    await loadModule('components/percentage_slider.js');
    await loadModule('utilities/_base.js');

    const events = [];
    document.addEventListener('yaffo:app-init-complete', (event) => events.push(event));

    await loadModule('app.js');
    const app = await window.PHOTO_ORGANIZER.appReady;

    expect(app).toBe(window.PHOTO_ORGANIZER);
    expect(events).toHaveLength(1);
    expect(events[0].detail.app).toBe(window.PHOTO_ORGANIZER);
    expect(window.PHOTO_ORGANIZER.COMPONENTS.navPagesBar).toBeTruthy();

    const input = document.querySelector('input[type="range"]');
    input.value = '0.25';
    input.dispatchEvent(new Event('input'));
    expect(document.querySelector('.percentage-slider-display span').textContent).toBe('25%');

    document.getElementById('new-automation-button').click();
    expect(document.getElementById('newAutomationModal').classList.contains('active')).toBe(true);
  });
});
