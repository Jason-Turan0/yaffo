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
