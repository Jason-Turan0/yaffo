import { loadModule } from '../support/load_module.js';

// Ask Yaffo's panel: rendering the polled transcript (collapsed tool lines, linked
// sources, localized errors), starting and switching conversations, and the
// floating panel's open/close.

const conversation = (id, title = `Conversation ${id}`, status = 'IDLE') =>
  ({ id, title, status, updated_at: null });

const fixture = () => {
  document.body.innerHTML = `
    <button id="assistant-open" aria-expanded="false">Ask</button>
    <button id="assistant-fab" aria-expanded="false">Ask</button>
    <aside id="assistant-panel" data-assistant-mode="floating" hidden>
      <select id="assistant-conversation"></select>
      <button id="assistant-new">New</button>
      <button id="assistant-delete" disabled>Delete</button>
      <button id="assistant-close">Close</button>
      <div class="chat-dialog" id="assistant-chat">
        <div id="assistant-chat-messages"></div>
        <div id="assistant-chat-status" hidden><span id="assistant-chat-elapsed"></span></div>
        <form id="assistant-chat-form">
          <textarea id="assistant-chat-message"></textarea>
          <button type="submit">Send</button>
          <button type="button" id="assistant-chat-cancel">Cancel</button>
        </form>
      </div>
    </aside>`;
};

const settle = async () => {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
};

const json = (body, status = 200) => Promise.resolve({
  ok: status < 400, status, json: () => Promise.resolve(body),
});

// Routes the fake server answers, keyed by "METHOD url".
const server = (routes) => {
  const fetchMock = vi.fn((url, init = {}) => {
    const key = `${init.method || 'GET'} ${url}`;
    if (!(key in routes)) throw new Error(`Unexpected request ${key}`);
    const route = routes[key];
    return typeof route === 'function' ? route(init) : json(route);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
};

const statusBody = (messages, status = 'IDLE') => ({ status, started_at: null, messages });

const start = async () => {
  window.APP_CONFIG.urls = {
    assistant_conversations: '/api/conversations',
    assistant_conversation_create: '/api/conversations/new',
  };
  window.PHOTO_ORGANIZER.confirmDialog = vi.fn(() => Promise.resolve(true));
  await loadModule('components/chat_dialog.js');
  const org = await loadModule('assistant/assistant.js');
  const api = org.assistant.init(window.testI18n, window.APP_CONFIG);
  await settle();
  return api;
};

beforeEach(() => {
  fixture();
  window.localStorage.clear();
});

afterEach(() => vi.unstubAllGlobals());

describe('assistant transcript', () => {
  it('collapses tool calls, links the sources under the answer, and localizes errors', async () => {
    window.localStorage.setItem('yaffo.assistant.conversation', '7');
    window.testI18n.t = (key, options = {}) =>
      (key === 'assistant:errors.model_error' ? 'Provider error' : options.defaultValue ?? key);
    server({
      'GET /api/conversations': { conversations: [conversation(7)] },
      'GET /assistant_conversation/conversation_id/7': statusBody([
        { type: 'user', content: 'How do I add folders?' },
        { type: 'tool', content: '', payload: { tool: 'search_docs', query: 'folders', count: 2, sources: [
          { title: 'Indexing', heading: 'Add Media Folders', url: 'https://docs/indexing/#add', scope: 'guide' },
          { title: 'Settings', heading: 'Settings', url: 'https://docs/settings/', scope: 'guide' },
        ] } },
        { type: 'tool', content: '', payload: { tool: 'read_doc', title: 'Indexing', sources: [
          { title: 'Indexing', heading: 'Add Media Folders', url: 'https://docs/indexing/#add', scope: 'guide' },
        ] } },
        { type: 'assistant', content: 'Open Settings.' },
        { type: 'user', content: 'Thanks' },
        { type: 'error', content: 'raw', payload: { code: 'model_error' } },
        { type: 'error', content: 'Unknown failure text', payload: { code: 'brand_new_code' } },
      ]),
    });

    await start();

    const feed = document.getElementById('assistant-chat-messages');
    const activity = feed.querySelector('details.assistant-activity');
    expect(activity.querySelectorAll('li')).toHaveLength(2);
    const answer = feed.querySelector('.chat-message-assistant');
    expect(answer.textContent).toBe('Open Settings.');
    const sources = answer.nextElementSibling;
    expect(sources.classList.contains('assistant-sources')).toBe(true);
    const links = [...sources.querySelectorAll('a')];
    expect(links.map((a) => a.href)).toEqual(['https://docs/indexing/#add', 'https://docs/settings/']);
    expect(links[0].textContent).toBe('Indexing › Add Media Folders');
    expect(links[0].target).toBe('_blank');
    const errors = [...feed.querySelectorAll('.chat-message-error')].map((e) => e.textContent);
    expect(errors).toEqual(['Provider error', 'Unknown failure text']);
  });

  it('starts on an empty state with suggestion chips when nothing was open', async () => {
    server({ 'GET /api/conversations': { conversations: [conversation(3)] } });

    await start();

    const chips = document.querySelectorAll('.assistant-suggestions .chip');
    expect(chips).toHaveLength(3);
    expect(document.getElementById('assistant-conversation').value).toBe('');
    expect(document.getElementById('assistant-delete').disabled).toBe(true);
  });
});

describe('assistant conversations', () => {
  it('sends the first message as a new conversation and remembers it', async () => {
    let created = false;
    const fetchMock = server({
      'GET /api/conversations': () => json({ conversations: created ? [conversation(12, 'Folders')] : [] }),
      'POST /api/conversations/new': () => {
        created = true;
        return json({ conversation: conversation(12, 'Folders', 'RUNNING') }, 202);
      },
      'GET /assistant_conversation/conversation_id/12': statusBody([{ type: 'user', content: 'Folders?' }]),
    });
    await start();

    document.getElementById('assistant-chat-message').value = 'Folders?';
    document.getElementById('assistant-chat-form').requestSubmit();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/assistant_conversation/conversation_id/12'));

    const create = fetchMock.mock.calls.find(([url]) => url === '/api/conversations/new');
    expect(JSON.parse(create[1].body)).toEqual({ message: 'Folders?' });
    expect(window.localStorage.getItem('yaffo.assistant.conversation')).toBe('12');
    expect(fetchMock).toHaveBeenCalledWith('/assistant_conversation/conversation_id/12');
  });

  it('shows the server error and keeps the text when sending is refused', async () => {
    server({
      'GET /api/conversations': { conversations: [] },
      'POST /api/conversations/new': () => json({ error: 'No API key configured.', code: 'api_key_missing' }, 400),
    });
    await start();

    const input = document.getElementById('assistant-chat-message');
    input.value = 'Hello';
    document.getElementById('assistant-chat-form').requestSubmit();

    await vi.waitFor(() => expect(window.notification.error).toHaveBeenCalledWith('No API key configured.'));
    expect(input.value).toBe('Hello');
  });

  it('deletes the open conversation after confirming', async () => {
    window.localStorage.setItem('yaffo.assistant.conversation', '5');
    let deleted = false;
    const fetchMock = server({
      'GET /api/conversations': () => json({ conversations: deleted ? [] : [conversation(5)] }),
      'GET /assistant_conversation/conversation_id/5': statusBody([]),
      'DELETE /assistant_delete/conversation_id/5': () => {
        deleted = true;
        return Promise.resolve({ ok: true, status: 204 });
      },
    });
    await start();
    expect(document.getElementById('assistant-delete').disabled).toBe(false);

    document.getElementById('assistant-delete').click();
    await vi.waitFor(() => expect(window.localStorage.getItem('yaffo.assistant.conversation')).toBeNull());

    expect(window.PHOTO_ORGANIZER.confirmDialog).toHaveBeenCalled();
    expect(fetchMock.mock.calls.some(([url, init]) =>
      url === '/assistant_delete/conversation_id/5' && init.method === 'DELETE')).toBe(true);
    expect(window.localStorage.getItem('yaffo.assistant.conversation')).toBeNull();
    expect(document.querySelectorAll('.assistant-suggestions .chip')).toHaveLength(3);
  });

  it('forgets a remembered conversation that no longer exists', async () => {
    window.localStorage.setItem('yaffo.assistant.conversation', '99');
    const fetchMock = server({ 'GET /api/conversations': { conversations: [conversation(1)] } });

    await start();

    expect(fetchMock.mock.calls.every(([url]) => !String(url).includes('/99'))).toBe(true);
    expect(window.localStorage.getItem('yaffo.assistant.conversation')).toBeNull();
  });
});

describe('floating panel', () => {
  it('the corner button opens the panel, hides while it is open, and takes focus back', async () => {
    server({ 'GET /api/conversations': { conversations: [] } });
    await start();
    const fab = document.getElementById('assistant-fab');
    const panel = document.getElementById('assistant-panel');

    fab.focus();
    fab.click();
    expect(panel.hidden).toBe(false);
    expect(fab.hidden).toBe(true);
    expect(fab.getAttribute('aria-expanded')).toBe('true');

    document.getElementById('assistant-close').click();
    expect(panel.hidden).toBe(true);
    expect(fab.hidden).toBe(false);
  });

  it('marks the open conversation as selected in the switcher', async () => {
    window.localStorage.setItem('yaffo.assistant.conversation', '2');
    server({
      'GET /api/conversations': { conversations: [conversation(1), conversation(2, 'Second', 'RUNNING')] },
      'GET /assistant_conversation/conversation_id/2': statusBody([]),
    });

    await start();

    const options = [...document.getElementById('assistant-conversation').options];
    expect(options.map((o) => o.value)).toEqual(['', '1', '2']);
    expect(options.filter((o) => o.hasAttribute('selected')).map((o) => o.value)).toEqual(['2']);
    expect(options[2].textContent).toBe('assistant:runningTitle');
  });

  it('opens from the navbar button and closes with Escape', async () => {
    server({ 'GET /api/conversations': { conversations: [] } });
    const api = await start();
    const panel = document.getElementById('assistant-panel');
    const button = document.getElementById('assistant-open');

    button.click();
    expect(panel.hidden).toBe(false);
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(api.isOpen()).toBe(true);

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(panel.hidden).toBe(true);
    expect(button.getAttribute('aria-expanded')).toBe('false');
  });
});

describe('assistant settings', () => {
  it('deletes all conversations after confirming and updates the count', async () => {
    document.body.innerHTML = `
      <p id="assistant-conversation-count" data-count="3">3 saved conversations</p>
      <button id="assistant-delete-all">Delete all</button>`;
    window.APP_CONFIG.urls = { assistant_delete_all: '/api/delete-all' };
    window.PHOTO_ORGANIZER.confirmDialog = vi.fn(() => Promise.resolve(true));
    server({ 'POST /api/delete-all': { deleted: 3 } });
    const org = await loadModule('assistant/settings.js');
    org.assistant.initSettings(window.testI18n, window.APP_CONFIG);

    document.getElementById('assistant-delete-all').click();
    await settle();

    expect(window.notification.success).toHaveBeenCalledWith('assistant:settings.deleted');
    expect(document.getElementById('assistant-conversation-count').dataset.count).toBe('0');
    expect(document.getElementById('assistant-delete-all').disabled).toBe(true);
  });
});
