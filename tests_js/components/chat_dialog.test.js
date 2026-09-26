import { loadModule } from '../support/load_module.js';

// The chat dialog controller's optional hooks: custom transcript rendering, what
// happens after a cancel, and load()/clear() for hosts that switch conversations.

const fixture = () => {
  document.body.innerHTML = `
    <div class="chat-dialog" id="c">
      <div id="c-messages"></div>
      <div id="c-status" hidden><span id="c-elapsed"></span></div>
      <form id="c-form">
        <textarea id="c-message"></textarea>
        <button type="submit">Send</button>
        <button type="button" id="c-cancel">Cancel</button>
      </form>
    </div>`;
};

const settle = async () => {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
};

const init = async (options) => {
  const org = await loadModule('components/chat_dialog.js');
  return org.COMPONENTS.initChatDialog('c', {
    startStatus: 'IDLE',
    runningStatus: 'RUNNING',
    statusUrl: () => '/status',
    onSend: vi.fn(() => Promise.resolve({ ok: true })),
    ...options,
  });
};

afterEach(() => vi.unstubAllGlobals());

describe('chat dialog hooks', () => {
  beforeEach(fixture);

  it('load() polls once and renders through renderMessages', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      status: 200,
      json: () => Promise.resolve({ status: 'IDLE', messages: [{ type: 'user', content: 'hi' }] }),
    })));
    const renderMessages = vi.fn((messages) => messages.map((m) => {
      const node = document.createElement('p');
      node.textContent = `custom:${m.content}`;
      return node;
    }));
    const chat = await init({ renderMessages });

    chat.load();
    await settle();

    expect(fetch).toHaveBeenCalledWith('/status');
    expect(document.getElementById('c-messages').textContent).toBe('custom:hi');
  });

  it('clear() shows the given nodes and reopens the input', async () => {
    const chat = await init({});
    const intro = document.createElement('div');
    intro.textContent = 'Ask me';

    chat.clear([intro]);

    expect(document.getElementById('c-messages').textContent).toBe('Ask me');
    expect(document.getElementById('c-message').disabled).toBe(false);
  });

  it('afterCancel replaces the default page reload', async () => {
    window.PHOTO_ORGANIZER.confirmDialog = vi.fn(() => Promise.resolve(true));
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      status: 200, json: () => Promise.resolve({ status: 'RUNNING', messages: [] }),
    })));
    const afterCancel = vi.fn();
    const onCancel = vi.fn(() => Promise.resolve());
    const chat = await init({ onCancel, afterCancel });
    chat.enterRunning();  // Cancel is only enabled while a run is active
    await settle();

    document.getElementById('c-cancel').click();
    await settle();

    expect(onCancel).toHaveBeenCalled();
    expect(afterCancel).toHaveBeenCalled();
  });
});
