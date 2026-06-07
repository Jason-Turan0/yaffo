window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

// Runtime widget errors, kept locally (in memory, per session) keyed by widget
// id. Not persisted — they're only sent along as context the next time the model
// generates, so it can fix code that threw.
window.PHOTO_ORGANIZER.widgetErrors = window.PHOTO_ORGANIZER.widgetErrors || {};

const BASE_GRID_OPTS = {
    column: 12,
    cellHeight: 80,
    margin: 8,
    float: true,
    columnOpts: { breakpoints: [{ w: 768, c: 1 }] }
};

// The widget iframe broker (window.PHOTO_ORGANIZER.initWidgetBroker) lives in
// widget_broker.js — the host-side counterpart of the in-iframe widget_api.js.

window.PHOTO_ORGANIZER.initPresentationGrid = () => {
    return GridStack.init({ ...BASE_GRID_OPTS, staticGrid: true });
};

window.PHOTO_ORGANIZER.initDesignGrid = (pageId, config) => {
    const grid = GridStack.init({ ...BASE_GRID_OPTS, handle: '.widget-header' });

    // Generated/edited widget content the client holds but hasn't saved, keyed by
    // widget id. Nothing is persisted until Save sends these to the server.
    const drafts = new Map();

    // The full widget set for Save: layout for every grid item, plus content for
    // any the client holds as a draft (untouched saved widgets send layout only,
    // so the server keeps their stored content).
    const getWidgets = () => grid.engine.nodes.map((node) => {
        const id = node.id;
        const titleInput = node.el.querySelector('.widget-title-input');
        const layout = { id, x: node.x, y: node.y, w: node.w, h: node.h };
        if (titleInput) layout.title = titleInput.value;
        const content = drafts.get(id);
        return content ? { ...content, ...layout } : layout;
    });

    const savePage = async () => {
        await fetch(config.buildUrl('pages_update', { page_id: pageId }), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: document.getElementById('page-title').value,
                theme_prompt: document.getElementById('page-theme').value,
                show_title: document.getElementById('page-show-title').checked,
                widgets: getWidgets()
            })
        });
        window.location.href = config.buildUrl('pages_detail', { page_id: pageId });
    };

    // Wire per-widget controls. The title is a display span (so the header
    // stays freely draggable); the edit pencil reveals an input only while
    // renaming, and its mousedown is stopped from starting a drag.
    const wireWidget = (el) => {
        const titleSpan = el.querySelector('.widget-title');
        const titleInput = el.querySelector('.widget-title-input');
        const editButton = el.querySelector('.widget-edit');
        if (titleSpan && titleInput && editButton) {
            const endEdit = () => {
                titleSpan.textContent = titleInput.value.trim() || 'Untitled widget';
                titleInput.hidden = true;
                titleSpan.hidden = false;
                editButton.hidden = false;
            };
            editButton.addEventListener('mousedown', (event) => event.stopPropagation());
            editButton.addEventListener('click', (event) => {
                event.stopPropagation();
                titleSpan.hidden = true;
                editButton.hidden = true;
                titleInput.hidden = false;
                titleInput.focus();
                titleInput.select();
            });
            titleInput.addEventListener('mousedown', (event) => event.stopPropagation());
            titleInput.addEventListener('blur', endEdit);
            titleInput.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    titleInput.blur();
                }
            });
        }
        const deleteButton = el.querySelector('.widget-delete');
        if (deleteButton) {
            deleteButton.addEventListener('click', async (event) => {
                event.stopPropagation();
                const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
                    title: 'Delete Widget',
                    message: 'Remove this widget?',
                    confirmText: 'Delete',
                    confirmClass: 'btn-danger'
                });
                if (!confirmed) return;
                await fetch(
                    config.buildUrl('pages_delete_widget', { page_id: pageId, widget_id: deleteButton.dataset.widgetId }),
                    { method: 'POST' }
                );
                grid.removeWidget(el);
            });
        }
    };

    const addWidgetEl = (html, pos = {}) => {
        const wrapper = document.createElement('div');
        wrapper.innerHTML = html.trim();
        const el = wrapper.firstElementChild;
        // Honor an explicit position from the model; otherwise place at the bottom
        // of the current (live) layout so it never collides with or displaces
        // existing widgets, regardless of unsaved moves.
        const x = Number.isInteger(pos.x) ? pos.x : 0;
        const y = Number.isInteger(pos.y) ? pos.y : grid.getRow();
        el.setAttribute('gs-x', String(x));
        el.setAttribute('gs-y', String(y));
        grid.addWidget(el);
        wireWidget(el);
    };

    const newWidgetId = () =>
        (window.crypto && crypto.randomUUID ? crypto.randomUUID() : String(Date.now())).replace(/-/g, '');

    // Manual add: an empty client-side draft, just like a generated one — nothing
    // is persisted until Save.
    const addWidget = () => addDraftWidget({
        id: newWidgetId(),
        title: 'New Widget',
        data_query: {},
        html: '',
        css: '',
        js: '',
        grid_w: 8,
        grid_h: 4,
        state: {}
    });

    const scrollConversation = () => {
        const messages = document.getElementById('conversation-messages');
        if (messages) messages.scrollTop = messages.scrollHeight;
    };

    const addChatMessage = (messagesEl, role, content, before = null) => {
        const el = document.createElement('div');
        el.className = `chat-message chat-message-${role}`;
        el.textContent = content;
        messagesEl.insertBefore(el, before);
        return el;
    };

    // A spinning status row pinned after the last message while the run is live;
    // its label tracks the current step. Assistant replies insert above it.
    const addStatusSpinner = (messagesEl, text) => {
        const el = document.createElement('div');
        el.className = 'chat-status';
        const spinner = document.createElement('span');
        spinner.className = 'chat-spinner';
        const label = document.createElement('span');
        label.className = 'chat-status-text';
        label.textContent = text;
        el.append(spinner, label);
        messagesEl.appendChild(el);
        return el;
    };

    // Render a draft widget's grid-item shell from its content (server resolves
    // its data and inlines the frame as srcdoc — nothing is persisted).
    const renderDraftShell = async (content) => {
        const response = await fetch(config.buildUrl('pages_widget_preview', { page_id: pageId }), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(content)
        });
        return await response.text();
    };

    // A generated widget: hold its content and drop it on the grid (at the model's
    // position if it gave one, else the bottom).
    const addDraftWidget = async (content) => {
        drafts.set(content.id, content);
        addWidgetEl(await renderDraftShell(content), { x: content.grid_x, y: content.grid_y });
    };

    // An edited widget: hold the new content and swap the item's contents in place,
    // or add it if it isn't on the grid yet. The model may also reposition on edit —
    // if it supplied coordinates, move the item; otherwise keep its grid position.
    const updateDraftWidget = async (content) => {
        drafts.set(content.id, content);
        const existing = document.querySelector(`.grid-stack-item[gs-id="${content.id}"]`);
        if (!existing) {
            addWidgetEl(await renderDraftShell(content), { x: content.grid_x, y: content.grid_y });
            return;
        }
        const wrapper = document.createElement('div');
        wrapper.innerHTML = (await renderDraftShell(content)).trim();
        existing.querySelector('.grid-stack-item-content')
            .replaceWith(wrapper.firstElementChild.querySelector('.grid-stack-item-content'));
        wireWidget(existing);
        if (Number.isInteger(content.grid_x) && Number.isInteger(content.grid_y)) {
            grid.update(existing, { x: content.grid_x, y: content.grid_y });
        }
    };

    // Read a newline-delimited JSON stream, invoking onRecord per record. Awaits
    // onRecord so records (incl. widget fetches) apply in order, never racing.
    const readNdjson = async (response, onRecord) => {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let nl;
            while ((nl = buffer.indexOf('\n')) >= 0) {
                const line = buffer.slice(0, nl).trim();
                buffer = buffer.slice(nl + 1);
                if (line) await onRecord(JSON.parse(line));
            }
        }
        const tail = (buffer + decoder.decode()).trim();
        if (tail) await onRecord(JSON.parse(tail));
    };

    const sendMessage = async (event) => {
        event.preventDefault();
        const input = document.getElementById('conversation-message');
        const sendButton = document.querySelector('#conversation-form button[type="submit"]');
        const message = input.value.trim();
        if (!message) return;
        input.value = '';

        const messagesEl = document.getElementById('conversation-messages');
        addChatMessage(messagesEl, 'user', message);
        // A spinner pinned after the last message; assistant replies insert above
        // it, its label tracks the current step, and it's removed when done.
        const status = addStatusSpinner(messagesEl, 'Thinking…');
        const statusLabel = status.querySelector('.chat-status-text');
        // Lock Send for the whole run so a second request can't overlap.
        if (sendButton) sendButton.disabled = true;
        scrollConversation();

        const apply = async (record) => {
            if (record.event === 'message') {
                addChatMessage(messagesEl, 'assistant', record.content, status);
            } else if (record.event === 'status') {
                statusLabel.textContent = record.text;
            } else if (record.event === 'widget_new') {
                await addDraftWidget(record.widget);
            } else if (record.event === 'widget_updated') {
                await updateDraftWidget(record.widget);
            } else if (record.event === 'done') {
                status.remove();
            }
            scrollConversation();
        };

        try {
            const response = await fetch(config.buildUrl('pages_chat', { page_id: pageId }), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // Send current widget content (drafts included) so the model edits
                // with sight of the real html/css/js instead of guessing.
                body: JSON.stringify({
                    message,
                    widgets: getWidgets(),
                    widget_errors: window.PHOTO_ORGANIZER.widgetErrors
                })
            });
            await readNdjson(response, apply);
        } finally {
            status.remove();  // safety if the stream ended without a 'done'
            if (sendButton) sendButton.disabled = false;
            scrollConversation();
        }
    };

    const gridEl = document.querySelector('.grid-stack');
    grid.on('dragstart resizestart', () => gridEl.classList.add('is-interacting'));
    grid.on('dragstop resizestop', () => gridEl.classList.remove('is-interacting'));

    const addButton = document.getElementById('add-widget-button');
    if (addButton) {
        addButton.addEventListener('click', addWidget);
    }

    const saveButton = document.getElementById('save-page-button');
    if (saveButton) {
        saveButton.addEventListener('click', savePage);
    }

    const conversationForm = document.getElementById('conversation-form');
    if (conversationForm) {
        conversationForm.addEventListener('submit', sendMessage);
    }

    grid.engine.nodes.forEach((node) => wireWidget(node.el));
    scrollConversation();

    return { grid };
};