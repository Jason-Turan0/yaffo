import { loadModule } from '../support/load_module.js';

// A person's faces page: select face cards, then "Remove Selected" or "Move Selected
// to" another person. Both copy the selection into their form as hidden `faces`
// inputs and submit it natively after the global confirm dialog.

const page = ({ targets = [{ id: 2, name: 'Nathan' }], selectedTarget = '' } = {}) => {
  document.body.innerHTML = `
    <form id="remove-form" method="POST" action="/people/1/faces/remove">
      <div id="selected-faces-container"></div>
      <button type="button" id="remove-selected-faces"></button>
    </form>
    <form id="move-form" method="POST" action="/people/1/faces/move">
      <div id="move-faces-container"></div>
      <select name="target_person" id="move-target-person">
        <option value="">-- Select person --</option>
        ${targets.map((t) => `<option value="${t.id}" ${String(t.id) === selectedTarget ? 'selected' : ''}>${t.name}</option>`).join('')}
      </select>
      <button type="button" id="move-selected-faces"></button>
    </form>
    <div class="faces-grid">
      ${[11, 12, 13].map((id) => `
        <div class="face-card" data-face-id="${id}" data-similarity="" data-date="">
          <input type="checkbox" name="faces" value="${id}" style="display:none;">
        </div>`).join('')}
    </div>`;
};

const init = async () => (await loadModule('people/faces.js')).people.initFaces(window.testI18n, {});

const click = (selector) => document.querySelector(selector).click();

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

describe('people faces — move selected', () => {
  beforeEach(() => {
    window.PHOTO_ORGANIZER.confirmDialog = vi.fn(() => Promise.resolve(true));
  });

  it('submits the selected faces and target person after confirming', async () => {
    page({ selectedTarget: '2' });
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {});
    await init();

    click('.face-card[data-face-id="11"]');
    click('.face-card[data-face-id="13"]');
    click('#move-selected-faces');
    await vi.waitFor(() => expect(submit).toHaveBeenCalled());

    const form = submit.mock.contexts[0];
    expect(form.id).toBe('move-form');
    expect([...form.querySelectorAll('#move-faces-container input[name="faces"]')].map((i) => i.value))
      .toEqual(['11', '13']);
    expect(form.querySelector('#move-target-person').value).toBe('2');
    expect(window.PHOTO_ORGANIZER.confirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ message: expect.stringContaining('people:faces.moveMessage') }),
    );
  });

  it('warns instead of moving when no face is selected', async () => {
    page({ selectedTarget: '2' });
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {});
    await init();

    click('#move-selected-faces');
    await flush();

    expect(window.notification.warning).toHaveBeenCalledWith('people:faces.moveSelectRequired');
    expect(submit).not.toHaveBeenCalled();
  });

  it('warns instead of moving when no person is chosen', async () => {
    page();
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {});
    await init();

    click('.face-card[data-face-id="12"]');
    click('#move-selected-faces');
    await flush();

    expect(window.notification.warning).toHaveBeenCalledWith('people:faces.movePersonRequired');
    expect(submit).not.toHaveBeenCalled();
  });

  it('does not move when the confirm dialog is cancelled', async () => {
    window.PHOTO_ORGANIZER.confirmDialog = vi.fn(() => Promise.resolve(false));
    page({ selectedTarget: '2' });
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {});
    await init();

    click('.face-card[data-face-id="12"]');
    click('#move-selected-faces');
    await flush();

    expect(submit).not.toHaveBeenCalled();
  });

  it('still removes the selection through its own form', async () => {
    page();
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {});
    await init();

    click('.face-card[data-face-id="12"]');
    click('#remove-selected-faces');
    await vi.waitFor(() => expect(submit).toHaveBeenCalled());

    const form = submit.mock.contexts[0];
    expect(form.id).toBe('remove-form');
    expect([...form.querySelectorAll('#selected-faces-container input')].map((i) => i.value)).toEqual(['12']);
  });
});
