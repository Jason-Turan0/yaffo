import { loadModule } from '../support/load_module.js';

const loadCollapsiblePanel = async () => {
  await loadModule('components/collapsible_panel.js');
  return window.togglePanel;
};

describe('togglePanel', () => {
  it('expands a collapsed panel', async () => {
    document.body.innerHTML = `
      <section data-panel-id="filters">
        <button class="panel-header" aria-expanded="false"></button>
        <div class="panel-content" style="display: none"></div>
      </section>`;
    const togglePanel = await loadCollapsiblePanel();

    togglePanel('filters');

    expect(document.querySelector('.panel-header').getAttribute('aria-expanded')).toBe('true');
    expect(document.querySelector('.panel-content').style.display).toBe('block');
  });

  it('collapses an expanded panel', async () => {
    document.body.innerHTML = `
      <section data-panel-id="filters">
        <button class="panel-header" aria-expanded="true"></button>
        <div class="panel-content" style="display: block"></div>
      </section>`;
    const togglePanel = await loadCollapsiblePanel();

    togglePanel('filters');

    expect(document.querySelector('.panel-header').getAttribute('aria-expanded')).toBe('false');
    expect(document.querySelector('.panel-content').style.display).toBe('none');
  });

  it('no-ops when the panel is absent', async () => {
    const togglePanel = await loadCollapsiblePanel();

    expect(() => togglePanel('missing')).not.toThrow();
  });

  it('no-ops when required children are absent', async () => {
    document.body.innerHTML = '<section data-panel-id="empty"></section>';
    const togglePanel = await loadCollapsiblePanel();

    expect(() => togglePanel('empty')).not.toThrow();
  });
});
