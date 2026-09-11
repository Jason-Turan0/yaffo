import { loadModule } from '../support/load_module.js';

const loadAutomations = async () => {
  await loadModule('utilities/automations.js');
  return window.PHOTO_ORGANIZER;
};

const triggerFixture = () => {
  document.body.innerHTML = `
    <div id="automation-triggers">
      <button type="button" class="js-add-schedule">Add schedule</button>
      <button type="button" class="js-edit-schedule" data-cron-value="0 9 * * 1" data-trigger-id="trigger-1">Edit</button>
      <button type="button" class="js-add-event">Add event</button>
      <button type="button" class="js-cancel">Cancel</button>
      <div class="automation-trigger-add">
        <input type="hidden" name="edit_trigger_id">
        <h3 class="schedule-editor-title"></h3>
        <div data-cron-builder></div>
        <button type="submit" class="js-save-schedule"></button>
        <p class="schedule-editor-error"></p>
      </div>
    </div>`;
  Element.prototype.scrollIntoView = vi.fn();
};

describe('automations trigger editor', () => {
  it('opens schedule editors with the injected cron builder', async () => {
    triggerFixture();
    const PO = await loadAutomations();
    const cronBuilder = {
      initAll: vi.fn(),
      reset: vi.fn(),
      setCron: vi.fn(),
    };

    PO.automations.initTriggerEditor(window.testI18n, cronBuilder);
    document.querySelector('.js-add-schedule').click();

    const area = document.querySelector('.automation-trigger-add');
    const mount = document.querySelector('[data-cron-builder]');
    expect(cronBuilder.initAll).toHaveBeenCalledWith(mount);
    expect(cronBuilder.reset).toHaveBeenCalledWith(mount);
    expect(area.classList.contains('adding-schedule')).toBe(true);

    document.querySelector('.js-edit-schedule').click();

    expect(document.querySelector('[name="edit_trigger_id"]').value).toBe('trigger-1');
    expect(cronBuilder.setCron).toHaveBeenCalledWith(mount, '0 9 * * 1');
  });
});

describe('automation dry-run results', () => {
  it('keeps raw action details in a scroll container when details are toggled', async () => {
    document.body.innerHTML = `
      <button id="automation-test-button">Test</button>
      <div id="automation-test-result" hidden></div>`;
    const PO = await loadAutomations();
    PO.pickFolder = vi.fn().mockResolvedValue('/photos');
    const longPath = '/photos/' + 'long-directory'.repeat(30);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, code_source: 'published', context: { media_item_ids: [1] },
        actions: [{name: 'move_media_items', summary: 'Move photo', args: [longPath]}] }),
    }));
    try {
      PO.automations.initAutomationTest('test', {buildUrl: () => '/test-files'}, '/photos', window.testI18n);
      document.getElementById('automation-test-button').click();
      await vi.waitFor(() => expect(document.querySelector('.automation-test-table')).not.toBeNull());
      const result = document.getElementById('automation-test-result');
      const table = result.querySelector('.table-container .automation-test-table');
      expect(table.textContent).toContain(longPath);
      const toggle = result.querySelector('input[type="checkbox"]');
      toggle.click();
      expect(result.classList.contains('show-details')).toBe(true);
      toggle.click();
      expect(result.classList.contains('show-details')).toBe(false);
      expect(result.querySelector('.table-container .automation-test-table')).toBe(table);
      expect(document.getElementById('automation-test-button').disabled).toBe(false);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
