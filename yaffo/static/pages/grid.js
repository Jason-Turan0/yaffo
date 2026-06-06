window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

const BASE_GRID_OPTS = {
    column: 12,
    cellHeight: 80,
    margin: 8,
    float: true,
    columnOpts: { breakpoints: [{ w: 768, c: 1 }] }
};

// Broker for widget iframes that run live, server-side queries. The sandboxed
// frames can't fetch (connect-src 'none'), so they postMessage a query up to the
// parent, which fetches and posts the rows back. Used in both modes.
window.PHOTO_ORGANIZER.initWidgetBroker = (pageId, config) => {
    window.addEventListener('message', async (event) => {
        const msg = event.data || {};
        if (msg.type !== 'yaffo:query') return;
        const frame = [...document.querySelectorAll('.widget-frame')]
            .find((f) => f.contentWindow === event.source);
        if (!frame) return;
        let data = null;
        try {
            const response = await fetch(
                config.buildUrl('pages_widget_query', { page_id: pageId, widget_id: frame.dataset.widgetId }),
                { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: msg.query }) }
            );
            data = (await response.json()).data;
        } catch (e) {
            data = null;
        }
        frame.contentWindow.postMessage({ type: 'yaffo:result', requestId: msg.requestId, data }, '*');
    });
};

window.PHOTO_ORGANIZER.initPresentationGrid = () => {
    return GridStack.init({ ...BASE_GRID_OPTS, staticGrid: true });
};

window.PHOTO_ORGANIZER.initDesignGrid = (pageId, config) => {
    const grid = GridStack.init({ ...BASE_GRID_OPTS, handle: '.widget-header' });

    const getLayout = () => grid.engine.nodes.map((node) => {
        const titleInput = node.el.querySelector('.widget-title-input');
        return {
            id: Number(node.id),
            x: node.x,
            y: node.y,
            w: node.w,
            h: node.h,
            title: titleInput ? titleInput.value : undefined
        };
    });

    const savePage = async () => {
        await fetch(config.buildUrl('pages_update', { page_id: pageId }), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: document.getElementById('page-title').value,
                theme_prompt: document.getElementById('page-theme').value,
                show_title: document.getElementById('page-show-title').checked,
                layout: getLayout()
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

    const addWidgetEl = (html) => {
        const wrapper = document.createElement('div');
        wrapper.innerHTML = html.trim();
        const el = wrapper.firstElementChild;
        // Place at the bottom of the current (live) layout so it never collides
        // with or displaces existing widgets, regardless of unsaved moves.
        el.setAttribute('gs-x', '0');
        el.setAttribute('gs-y', String(grid.getRow()));
        grid.addWidget(el);
        wireWidget(el);
    };

    const addWidget = async () => {
        const response = await fetch(config.buildUrl('pages_add_widget', { page_id: pageId }), { method: 'POST' });
        addWidgetEl(await response.text());
    };

    const scrollConversation = () => {
        const messages = document.getElementById('conversation-messages');
        if (messages) messages.scrollTop = messages.scrollHeight;
    };

    const sendMessage = async (event) => {
        event.preventDefault();
        const input = document.getElementById('conversation-message');
        const message = input.value.trim();
        if (!message) return;
        input.value = '';
        const response = await fetch(config.buildUrl('pages_chat', { page_id: pageId }), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message })
        });
        const data = await response.json();
        document.getElementById('conversation-messages').innerHTML = data.messages_html;
        if (data.widget_html) {
            addWidgetEl(data.widget_html);
        }
        scrollConversation();
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