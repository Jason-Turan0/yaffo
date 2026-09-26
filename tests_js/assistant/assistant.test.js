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
  window.sessionStorage.clear();
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

describe('diagnostics activity', () => {
  it('expands a check to exactly what was sent and a script to its source', async () => {
    window.localStorage.setItem('yaffo.assistant.conversation', '4');
    const keys = [];
    window.testI18n.t = (key, options = {}) => {
      keys.push([key, options]);
      return key;
    };
    server({
      'GET /api/conversations': { conversations: [conversation(4)] },
      'GET /assistant_conversation/conversation_id/4': statusBody([
        { type: 'user', content: 'Why is 2017 missing?' },
        { type: 'tool', content: '', payload: {
          tool: 'read_log', args: { name: 'background_tasks.log' }, detail: '22:07 ERROR boom', count: 1 } },
        { type: 'tool', content: '', payload: {
          tool: 'run_script', purpose: 'Count 2017', script: 'len(rows)', detail: 'Value: 0' } },
        { type: 'tool', content: '', payload: { tool: 'stat_path', args: { media_dir_id: 'm1' }, error: true, detail: 'x' } },
        { type: 'assistant', content: 'The drive stopped responding.' },
      ]),
    });

    await start();

    const activity = document.querySelector('details.assistant-activity');
    expect(activity.querySelector('summary').textContent).toBe('assistant:activity.steps');
    const [log, script, failed] = activity.querySelectorAll(':scope > ul > li');
    expect(log.querySelector('summary').textContent).toBe('assistant:activity.tools.read_log');
    expect(log.querySelector('pre').textContent).toBe('22:07 ERROR boom');
    expect(log.querySelector('.assistant-tool-note')).not.toBeNull();
    expect(keys).toContainEqual(['assistant:activity.tools.read_log', expect.objectContaining({ name: 'background_tasks.log' })]);

    expect(script.querySelector('.assistant-tool-script pre').textContent).toBe('len(rows)');
    expect(keys).toContainEqual(['assistant:activity.script', { purpose: 'Count 2017' }]);
    expect(failed.classList.contains('assistant-tool-error')).toBe(true);
    expect(keys.some(([key]) => key === 'assistant:activity.failed')).toBe(true);
  });
});

describe('empty conversation notice', () => {
  it('puts the notice from the panel template above the suggestions', async () => {
    document.getElementById('assistant-panel').insertAdjacentHTML('beforeend', `
      <template id="assistant-notice-template">
        <p class="assistant-notice">Uses M (P). <a href="/settings#assistant-section">Change in Settings</a></p>
      </template>`);
    server({ 'GET /api/conversations': { conversations: [] } });

    await start();

    const empty = document.querySelector('#assistant-chat-messages .assistant-empty');
    expect(empty.firstElementChild.classList.contains('assistant-notice')).toBe(true);
    expect(empty.querySelector('a').getAttribute('href')).toBe('/settings#assistant-section');

    // Every new conversation shows it again.
    document.getElementById('assistant-new').click();
    expect(document.querySelectorAll('.assistant-notice')).toHaveLength(1);
  });
});

describe('help me with this', () => {
  const withContextChip = () => {
    document.getElementById('assistant-panel').insertAdjacentHTML('beforeend', `
      <div id="assistant-context" hidden><span id="assistant-context-label"></span>
        <button id="assistant-context-remove">x</button></div>`);
    document.body.insertAdjacentHTML('beforeend', `
      <button data-assistant-help data-job-id="job-1" data-page="index_photos" data-error="Could not scan">Help</button>`);
  };

  it('opens a new conversation with the failed job attached and sends it once', async () => {
    withContextChip();
    window.localStorage.setItem('yaffo.assistant.conversation', '2');
    const fetchMock = server({
      'GET /api/conversations': { conversations: [conversation(2)] },
      'GET /assistant_conversation/conversation_id/2': statusBody([]),
      'POST /api/conversations/new': () => json({ conversation: conversation(8, 'Help', 'RUNNING') }, 202),
      'GET /assistant_conversation/conversation_id/8': statusBody([]),
    });
    await start();

    document.querySelector('[data-assistant-help]').click();

    const panel = document.getElementById('assistant-panel');
    expect(panel.hidden).toBe(false);
    const chip = document.getElementById('assistant-context');
    expect(chip.hidden).toBe(false);
    expect(chip.parentElement.id).toBe('assistant-chat-form');
    expect(document.getElementById('assistant-context-label').textContent).toBe('index_photos · job-1');
    expect(document.getElementById('assistant-chat-message').value).toBe('assistant:helpPrompt');

    document.getElementById('assistant-chat-form').requestSubmit();
    await vi.waitFor(() => expect(chip.hidden).toBe(true));
    const create = fetchMock.mock.calls.find(([url]) => url === '/api/conversations/new');
    expect(JSON.parse(create[1].body)).toEqual({
      message: 'assistant:helpPrompt',
      context: { page: 'index_photos', job_id: 'job-1', error: 'Could not scan' },
    });
  });

  it('the chip can be removed before sending', async () => {
    withContextChip();
    server({ 'GET /api/conversations': { conversations: [] } });
    const api = await start();

    api.openWithContext({ page: 'Settings', error_code: 'x' });
    document.getElementById('assistant-context-remove').click();
    expect(document.getElementById('assistant-context').hidden).toBe(true);
  });

  it('shows the context sent with an earlier message', async () => {
    window.localStorage.setItem('yaffo.assistant.conversation', '3');
    server({
      'GET /api/conversations': { conversations: [conversation(3)] },
      'GET /assistant_conversation/conversation_id/3': statusBody([
        { type: 'user', content: 'Why?', payload: { context: { page: 'Index Photos', error_code: 'scan_failed' } } },
      ]),
    });
    await start();

    expect(document.querySelector('.assistant-context-sent').textContent).toBe('Index Photos · scan_failed');
  });
});

describe('panel across page loads', () => {
  const matchMedia = (matches) => vi.stubGlobal('matchMedia', vi.fn(() => ({ matches })));

  it('reopens on the next page without taking focus, and forgets once closed', async () => {
    matchMedia(false);
    server({ 'GET /api/conversations': { conversations: [] } });
    await start();
    document.getElementById('assistant-open').click();
    expect(window.sessionStorage.getItem('yaffo.assistant.open')).toBe('true');

    // The next page: fresh DOM, same tab.
    fixture();
    const pageButton = document.createElement('button');
    document.body.appendChild(pageButton);
    pageButton.focus();
    await start();

    expect(document.getElementById('assistant-panel').hidden).toBe(false);
    expect(document.getElementById('assistant-fab').hidden).toBe(true);
    expect(document.getElementById('assistant-open').getAttribute('aria-expanded')).toBe('true');
    expect(document.activeElement).toBe(pageButton);

    document.getElementById('assistant-close').click();
    expect(window.sessionStorage.getItem('yaffo.assistant.open')).toBeNull();
    fixture();
    await start();
    expect(document.getElementById('assistant-panel').hidden).toBe(true);
  });

  it('stays closed on a phone-width screen, where it would cover the new page', async () => {
    matchMedia(true);
    window.sessionStorage.setItem('yaffo.assistant.open', 'true');
    server({ 'GET /api/conversations': { conversations: [] } });

    await start();

    expect(document.getElementById('assistant-panel').hidden).toBe(true);
  });
});

describe('panel width', () => {
  const withHandle = () => {
    document.getElementById('assistant-panel')
      .insertAdjacentHTML('afterbegin', '<div id="assistant-resize" tabindex="0"></div>');
  };
  const panelWidth = () => document.getElementById('assistant-panel').style.getPropertyValue('--assistant-panel-width');
  const pointer = (type, clientX) => {
    const Ctor = window.PointerEvent || window.MouseEvent;
    return new Ctor(type, { bubbles: true, button: 0, clientX, pointerId: 1 });
  };
  const drag = (from, to) => {
    const handle = document.getElementById('assistant-resize');
    handle.dispatchEvent(pointer('pointerdown', from));
    handle.dispatchEvent(pointer('pointermove', to));
    handle.dispatchEvent(pointer('pointerup', to));
  };

  beforeEach(() => {
    window.innerWidth = 1400;
    server({ 'GET /api/conversations': { conversations: [] } });
  });

  afterEach(() => { window.innerWidth = 1024; });

  it('drags wider toward the page and remembers the width', async () => {
    withHandle();
    await start();
    expect(panelWidth()).toBe('440px');

    drag(1000, 900);

    expect(panelWidth()).toBe('540px');
    expect(window.localStorage.getItem('yaffo.assistant.width')).toBe('540');
    expect(document.body.classList.contains('assistant-resizing')).toBe(false);

    fixture();
    withHandle();
    await start();
    expect(panelWidth()).toBe('540px');
  });

  it('clamps to the minimum and to leaving part of the page visible', async () => {
    withHandle();
    await start();
    drag(1000, 1400);
    expect(panelWidth()).toBe('320px');
    window.innerWidth = 900;
    drag(1000, 0);
    expect(panelWidth()).toBe('740px');
  });

  it('mirrors the drag direction in right-to-left layouts', async () => {
    document.documentElement.dir = 'rtl';
    document.getElementById('assistant-panel').style.direction = 'rtl';
    try {
      withHandle();
      await start();
      drag(400, 500);
      expect(panelWidth()).toBe('540px');
    } finally {
      document.documentElement.dir = '';
    }
  });

  it('resizes from the keyboard and resets on double-click', async () => {
    withHandle();
    await start();
    const handle = document.getElementById('assistant-resize');

    handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
    expect(panelWidth()).toBe('472px');
    expect(handle.getAttribute('aria-valuenow')).toBe('472');
    handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
    expect(panelWidth()).toBe('320px');

    handle.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    expect(panelWidth()).toBe('440px');
    expect(window.localStorage.getItem('yaffo.assistant.width')).toBeNull();
  });
});

describe('narrow navbar Menu', () => {
  it('closes the Menu and any page panel when the panel opens from it', async () => {
    const nav = { applyMenu: vi.fn(), closeContextPanels: vi.fn() };
    window.PHOTO_ORGANIZER.COMPONENTS.navPagesBar = nav;
    server({ 'GET /api/conversations': { conversations: [] } });
    try {
      await start();
      document.getElementById('assistant-open').click();

      expect(nav.applyMenu).toHaveBeenCalledWith(false);
      expect(nav.closeContextPanels).toHaveBeenCalled();
      expect(document.getElementById('assistant-panel').hidden).toBe(false);
    } finally {
      delete window.PHOTO_ORGANIZER.COMPONENTS.navPagesBar;
    }
  });

  it('returns focus to the Menu button on close, since the Menu item is hidden by then', async () => {
    document.body.insertAdjacentHTML('afterbegin', '<button id="nav-menu-toggle">Menu</button>');
    const menuToggle = document.getElementById('nav-menu-toggle');
    // jsdom has no layout: only the Menu button counts as visible.
    Object.defineProperty(menuToggle, 'offsetParent', { get: () => document.body });
    server({ 'GET /api/conversations': { conversations: [] } });
    await start();

    document.getElementById('assistant-open').click();
    document.getElementById('assistant-close').click();

    expect(document.activeElement).toBe(menuToggle);
  });
});

describe('app links', () => {
  it('shows the links a run made under its answer, before the doc sources', async () => {
    window.localStorage.setItem('yaffo.assistant.conversation', '6');
    server({
      'GET /api/conversations': { conversations: [conversation(6)] },
      'GET /assistant_conversation/conversation_id/6': statusBody([
        { type: 'user', content: 'Show me Chase in 2019' },
        { type: 'tool', content: '', payload: { tool: 'link_to_photos', args: { title: 'Chase in 2019' }, count: 12,
          links: [{ title: 'Chase in 2019', url: '/?person=10&year=2019' }] } },
        { type: 'tool', content: '', payload: { tool: 'read_doc', title: 'Browsing', sources: [
          { title: 'Browsing', heading: 'Filters', url: 'https://docs/browsing/#filters', scope: 'guide' },
        ] } },
        { type: 'assistant', content: 'Here are the 12 photos.' },
      ]),
    });

    await start();

    const answer = document.querySelector('.chat-message-assistant');
    const links = answer.nextElementSibling;
    expect(links.classList.contains('assistant-links')).toBe(true);
    const anchor = links.querySelector('a');
    expect(anchor.textContent).toBe('Chase in 2019');
    expect(anchor.getAttribute('href')).toBe('/?person=10&year=2019');
    expect(anchor.target).toBe('');
    expect(links.nextElementSibling.classList.contains('assistant-sources')).toBe(true);
  });
});


describe('contextual help availability', () => {
  it('does not register the error-toast help action on Settings', async () => {
    document.body.setAttribute('data-assistant-help-disabled', '');
    const setErrorAction = vi.fn();
    window.notification.setErrorAction = setErrorAction;
    server({ 'GET /api/conversations': { conversations: [] } });
    try {
      await start();
      expect(setErrorAction).not.toHaveBeenCalled();
    } finally {
      document.body.removeAttribute('data-assistant-help-disabled');
    }
  });
});


describe('automation run help', () => {
  it('handles a polled-in run row and sends its job and automation context', async () => {
    const fetchMock = server({
      'GET /api/conversations': { conversations: [] },
      'POST /api/conversations/new': () => json({ conversation: conversation(9, 'Help') }, 202),
      'GET /assistant_conversation/conversation_id/9': statusBody([]),
    });
    await start();
    document.body.insertAdjacentHTML('beforeend', `
      <button data-assistant-help data-job-id="run-1" data-automation="assign_location_name"
        data-page="Assign location name" data-error="2 errors">Help</button>`);
    document.querySelector('[data-assistant-help]').click();
    expect(document.getElementById('assistant-panel').hidden).toBe(false);
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
    document.getElementById('assistant-chat-form').requestSubmit();
    await vi.waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === '/api/conversations/new')).toBe(true));
    const create = fetchMock.mock.calls.find(([url]) => url === '/api/conversations/new');
    expect(JSON.parse(create[1].body).context).toEqual({
      job_id: 'run-1', automation: 'assign_location_name', page: 'Assign location name', error: '2 errors',
    });
  });
});
