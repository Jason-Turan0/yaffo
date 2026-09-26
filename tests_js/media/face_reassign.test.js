import { loadModule } from '../support/load_module.js';

// The photo detail screen's face popover: Clear takes the person off a face.

const config = { urls: { faces_assign: '/api/faces/assign', faces_unassign: '/api/faces/unassign' } };

const fixture = () => {
  document.body.innerHTML = `
    <div class="face-thumbnail" id="face-thumbnail-7" data-face-id="7" data-face-status="ASSIGNED"></div>
    <div class="face-thumbnail" id="face-thumbnail-8" data-face-id="8" data-face-status="UNASSIGNED"></div>`;
};

// The overlay component renders the content next to the thumbnail; a plain div will do.
const stubOverlay = () => {
  window.PHOTO_ORGANIZER.COMPONENTS.overlay = {
    init: vi.fn((targetId, content) => {
      const overlay = document.createElement('div');
      overlay.innerHTML = content;
      document.body.appendChild(overlay);
      return { overlay, close: vi.fn(() => overlay.remove()) };
    }),
  };
};

const json = (body, ok = true) => Promise.resolve({ ok, status: ok ? 200 : 409, json: () => Promise.resolve(body) });

const start = async () => {
  const org = await loadModule('media/face-reassign.js');
  stubOverlay();
  org.VIEW_PHOTO.initFaceReassign([{ id: 1, name: 'Ada' }], window.testI18n, config);
};

beforeEach(() => {
  fixture();
  vi.useFakeTimers({ toFake: ['setTimeout'] });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('face reassign Clear', () => {
  it('is offered on an assigned face and clears it', async () => {
    const fetchMock = vi.fn(() => json({ success: true, message: 'Cleared 1 face' }));
    vi.stubGlobal('fetch', fetchMock);
    const reload = vi.fn();
    vi.stubGlobal('location', { reload });
    await start();

    document.getElementById('face-thumbnail-7').click();
    const clear = document.querySelector('[data-action="clear"]');
    expect(clear.textContent).toBe('media:faces.clear');

    clear.click();
    await vi.waitFor(() => expect(window.notification.success).toHaveBeenCalledWith('Cleared 1 face'));
    expect(fetchMock).toHaveBeenCalledWith('/api/faces/unassign', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ faces: [7] }),
    }));
    vi.advanceTimersByTime(500);
    expect(reload).toHaveBeenCalled();
  });

  it('is not offered on an unassigned face', async () => {
    await start();
    document.getElementById('face-thumbnail-8').click();
    expect(document.querySelector('[data-action="clear"]')).toBeNull();
    expect(document.querySelector('[data-action="apply"]')).not.toBeNull();
  });

  it('shows the server error and lets the user try again', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json({ success: false, message: "This face isn't assigned to anyone" }, false)));
    await start();

    document.getElementById('face-thumbnail-7').click();
    const clear = document.querySelector('[data-action="clear"]');
    clear.click();

    await vi.waitFor(() => expect(window.notification.error).toHaveBeenCalledWith("This face isn't assigned to anyone"));
    expect(clear.disabled).toBe(false);
    expect(clear.textContent).toBe('media:faces.clear');
  });
});
