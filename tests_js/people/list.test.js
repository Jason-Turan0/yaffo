import { loadModule } from '../support/load_module.js';

// Deleting a person submits a form built in JS. A native submit bypasses the
// fetch/htmx hooks that attach X-CSRF-Token, so the form must carry the token
// as a csrf_token field or the server answers "Request not verified".

const config = () => ({
  urls: {},
  buildUrl: (endpoint, params = {}) => `/${endpoint}/${params.person_id}`,
  csrfToken: 'token-123',
});

const fakeModal = () => ({ element: document.createElement('div'), open: vi.fn(), close: vi.fn() });

describe('people list delete', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div class="people-table">
        <a href="#" data-action="delete" data-person-id="7" data-person-name="Ada">Delete</a>
      </div>`;
    window.PHOTO_ORGANIZER.COMPONENTS.modal = { init: vi.fn(fakeModal) };
    window.PHOTO_ORGANIZER.confirmDialog = vi.fn(() => Promise.resolve(true));
  });

  it('submits the delete form with the CSRF token', async () => {
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {});
    (await loadModule('people/list.js')).people.initList(window.testI18n, config());

    document.querySelector('[data-action="delete"]').click();
    await vi.waitFor(() => expect(submit).toHaveBeenCalled());

    const form = submit.mock.contexts[0];
    expect(form.method).toBe('post');
    expect(form.getAttribute('action')).toBe('/people_delete/7');
    expect(form.querySelector('input[name="csrf_token"]').value).toBe('token-123');
  });

  it('does not submit when the confirm dialog is cancelled', async () => {
    window.PHOTO_ORGANIZER.confirmDialog = vi.fn(() => Promise.resolve(false));
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {});
    (await loadModule('people/list.js')).people.initList(window.testI18n, config());

    document.querySelector('[data-action="delete"]').click();
    await Promise.resolve();
    await Promise.resolve();

    expect(submit).not.toHaveBeenCalled();
  });
});
