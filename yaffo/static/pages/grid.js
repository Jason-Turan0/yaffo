// @ts-check

/**
 * The in-flight generation phase for the design page.
 * @typedef {Object} Generation
 * @property {number} versionId
 * @property {string} status
 * @property {string | null} startedAt
 * @property {ReturnType<typeof setTimeout>} [pollTimer]
 * @property {ReturnType<typeof setInterval>} [elapsedTimer]
 *
 * A conversation-feed line from the polled transcript.
 * @typedef {Object} FeedMessage
 * @property {string} type
 * @property {string} content
 *
 * The polled version-status payload.
 * @typedef {Object} VersionStatusBody
 * @property {string} status
 * @property {string | null} started_at
 * @property {FeedMessage[]} messages
 * @property {Record<string, any>[]} widgets
 */

const pagesGridWindow = window;

pagesGridWindow.PHOTO_ORGANIZER = pagesGridWindow.PHOTO_ORGANIZER || {};
const pagesGridNamespace = pagesGridWindow.PHOTO_ORGANIZER.pages =
    /** @type {PagesNamespace} */ (pagesGridWindow.PHOTO_ORGANIZER.pages || {});

// Runtime widget errors, kept locally (in memory, per session) keyed by widget
// id. Not persisted — they're only sent along as context the next time the model
// generates, so it can fix code that threw.
pagesGridWindow.PHOTO_ORGANIZER.widgetErrors = pagesGridWindow.PHOTO_ORGANIZER.widgetErrors || {};

const BASE_GRID_OPTS = {
    column: 12,
    cellHeight: 80,
    margin: 8,
    float: true,
    columnOpts: { breakpoints: [{ w: 768, c: 1 }] }
};

const POLL_INTERVAL_MS = 1500;
const POLL_RETRY_MS = 3000;

// Page-version statuses (mirror PAGE_VERSION_STATUS_* in yaffo/db/models.py).
const STATUS = {
    IN_PROGRESS: 'IN_PROGRESS',
    READY: 'READY',
    FAILED: 'FAILED',
    ACCEPTED: 'ACCEPTED',
    CANCELLED: 'CANCELLED',
};

// The widget iframe broker (window.PHOTO_ORGANIZER.pages.initWidgetBroker) lives in
// widget_broker.js — the host-side counterpart of the in-iframe widget_api.js.

pagesGridNamespace.initPresentationGrid = () => {
    return GridStack.init({ ...BASE_GRID_OPTS, staticGrid: true });
};

// Design grid. The page edits exactly one version — `editVersionId` (its status is
// `startStatus`): the working draft if a generation has produced one, else the
// published version. Every edit targets that version, so there's no published-vs-
// working ambiguity. `ACCEPTED` means it's the live published version (idle editing);
// IN_PROGRESS/READY/FAILED mean it's a working draft (poll + review). A new draft is
// forked on the first chat message, and `generation.versionId` then tracks it.
// See docs/ai-page-builder-async-generation.md.
/**
 * @param {number} pageId
 * @param {number} editVersionId
 * @param {string} startStatus
 * @param {AppConfig} config
 * @param {I18nService} i18n
 * @returns {PageDesignGridApi}
 */
pagesGridNamespace.initDesignGrid = (pageId, editVersionId, startStatus, config, i18n) => {
    /**
     * @param {string} key
     * @param {Record<string, unknown>} [options]
     */
    const t = (key, options = {}) => i18n.t(key, options);
    const grid = GridStack.init({ ...BASE_GRID_OPTS, handle: '.widget-header' });

    // Generated/edited widget content the client holds but hasn't saved (manual
    // adds), keyed by widget id. Nothing is persisted until Save sends these to the
    // server.
    /** @type {Map<string, Record<string, any>>} */
    const drafts = new Map();

    const designLayout = document.querySelector('.page-design');
    const messagesEl = document.getElementById('conversation-messages');
    const messageInput = /** @type {HTMLInputElement | null} */ (document.getElementById('conversation-message'));
    const sendButton = /** @type {HTMLButtonElement | null} */ (document.querySelector('#conversation-form button[type="submit"]'));
    const addButton = /** @type {HTMLButtonElement | null} */ (document.getElementById('add-widget-button'));
    const saveButton = /** @type {HTMLButtonElement | null} */ (document.getElementById('save-page-button'));
    const cancelButton = /** @type {HTMLButtonElement | null} */ (document.getElementById('conversation-cancel'));
    const statusBar = document.getElementById('conversation-status');
    const elapsedEl = document.getElementById('conversation-elapsed');

    // The version being edited + its phase: { versionId, status, startedAt,
    // pollTimer, elapsedTimer }. versionId is always a real version (the published
    // one when status is ACCEPTED); status drives the UI phase. enterRunning swaps in
    // the working version once a generation starts.
    /** @type {Generation} */
    let generation = { versionId: editVersionId, status: startStatus, startedAt: null };
    // id -> JSON signature of the version widget last rendered, so polls only
    // re-render widgets that actually changed.
    /** @type {Map<string, string>} */
    const rendered = new Map();

    // The full widget set for Save: layout for every grid item, plus content for
    // any the client holds as a draft (untouched saved widgets send layout only,
    // so the server keeps their stored content).
    const getWidgets = () => grid.engine.nodes.map((/** @type {any} */ node) => {
        const id = node.id;
        const titleInput = node.el.querySelector('.widget-title-input');
        /** @type {Record<string, any>} */
        const layout = { id, x: node.x, y: node.y, w: node.w, h: node.h };
        if (titleInput) layout.title = titleInput.value;
        const content = drafts.get(id);
        return content ? { ...content, ...layout } : layout;
    });

    const savePage = async () => {
        const body = {
            title: /** @type {HTMLInputElement} */ (document.getElementById('page-title')).value,
            subtitle: /** @type {HTMLInputElement} */ (document.getElementById('page-subtitle')).value,
            show_title: /** @type {HTMLInputElement} */ (document.getElementById('page-show-title')).checked,
            tab_order: parseInt(/** @type {HTMLInputElement} */ (document.getElementById('page-tab-order')).value, 10) || 0,
            widgets: getWidgets()
        };
        const response = await fetch(config.buildUrl('pages_update', { page_id: pageId }), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (!response.ok) return;
        window.location.href = config.buildUrl('pages_detail', { page_id: pageId });
    };

    // Wire per-widget controls. The title is a display span (so the header
    // stays freely draggable); the edit pencil reveals an input only while
    // renaming, and its mousedown is stopped from starting a drag.
    /** @param {Element} el */
    const wireWidget = (el) => {
        const titleSpan = /** @type {HTMLElement | null} */ (el.querySelector('.widget-title'));
        const titleInput = /** @type {HTMLInputElement | null} */ (el.querySelector('.widget-title-input'));
        const editButton = /** @type {HTMLElement | null} */ (el.querySelector('.widget-edit'));
        if (titleSpan && titleInput && editButton) {
            const endEdit = () => {
                titleSpan.textContent = titleInput.value.trim() || t('pages:widgets.untitled');
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
        const deleteButton = /** @type {HTMLElement | null} */ (el.querySelector('.widget-delete'));
        if (deleteButton) {
            deleteButton.addEventListener('click', async (event) => {
                event.stopPropagation();
                const confirmed = await pagesGridWindow.PHOTO_ORGANIZER.confirmDialog({
                    title: t('pages:widgets.deleteTitle'),
                    message: t('pages:widgets.deleteMessage'),
                    confirmText: t('common:delete'),
                    confirmClass: 'btn-danger'
                });
                if (!confirmed) return;
                // Delete from the version being edited (the working draft, or the
                // published version when idle) — never a guess.
                await fetch(
                    config.buildUrl('pages_version_delete_widget', {
                        page_id: pageId, version_id: generation.versionId, widget_id: deleteButton.dataset.widgetId
                    }),
                    { method: 'POST' }
                );
                grid.removeWidget(el);
                rendered.delete(deleteButton.dataset.widgetId ?? '');
            });
        }
    };

    /**
     * @param {string} html
     * @param {{ x?: number, y?: number }} [pos]
     */
    const addWidgetEl = (html, pos = {}) => {
        const wrapper = document.createElement('div');
        wrapper.innerHTML = html.trim();
        const el = /** @type {Element} */ (wrapper.firstElementChild);
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
        (pagesGridWindow.crypto && crypto.randomUUID ? crypto.randomUUID() : String(Date.now())).replace(/-/g, '');

    // Manual add: an empty client-side draft — nothing is persisted until Save.
    const addWidget = () => addDraftWidget({
        id: newWidgetId(),
        title: t('pages:widgets.new'),
        data_query: {},
        html: '',
        css: '',
        js: '',
        grid_w: 8,
        grid_h: 4,
        state: {}
    });

    const scrollConversation = () => {
        if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
    };

    // Render a widget's grid-item shell from its content (server resolves its data
    // and inlines the frame as srcdoc — nothing is persisted by this call).
    /** @param {Record<string, any>} content */
    const renderShell = async (content) => {
        const response = await fetch(config.buildUrl('pages_widget_preview', { page_id: pageId }), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(content)
        });
        return await response.text();
    };

    // Hold content as a manual draft and drop it on the grid (at the given position
    // if any, else the bottom).
    /** @param {Record<string, any>} content */
    const addDraftWidget = async (content) => {
        drafts.set(content.id, content);
        addWidgetEl(await renderShell(content), { x: content.grid_x, y: content.grid_y });
    };

    // Render content into the grid: swap an existing item's contents in place, or
    // add it if it isn't on the grid yet. Used by manual edits and by the locked
    // reconcile. The model may reposition on edit — if it supplied coordinates,
    // move the item; otherwise keep its grid position.
    /** @param {Record<string, any>} content */
    const renderWidget = async (content) => {
        const existing = document.querySelector(`.grid-stack-item[gs-id="${content.id}"]`);
        if (!existing) {
            addWidgetEl(await renderShell(content), { x: content.grid_x, y: content.grid_y });
            return;
        }
        const wrapper = document.createElement('div');
        wrapper.innerHTML = (await renderShell(content)).trim();
        /** @type {Element} */ (existing.querySelector('.grid-stack-item-content'))
            .replaceWith(/** @type {Element} */ (/** @type {Element} */ (wrapper.firstElementChild).querySelector('.grid-stack-item-content')));
        wireWidget(existing);
        if (Number.isInteger(content.grid_x) && Number.isInteger(content.grid_y)) {
            grid.update(existing, { x: content.grid_x, y: content.grid_y });
        }
    };

    // ---- Generation ----
    const isRunning = () => {
        return generation.status === STATUS.IN_PROGRESS;
    }

    // Reflect the generation phase in the UI. Phase from `generation`:
    //   idle (none)            — normal editing; Save commits to the published page.
    //   running (IN_PROGRESS)  — the model is mutating the version: lock the grid and
    //                            Send; only Cancel is available; status bar ticks.
    //   review (READY/FAILED)  — the draft is the user's again: move widgets, send
    //                            follow-ups to iterate; Save publishes (READY only),
    //                            Cancel discards.
    const refreshUi = () => {
        const running = isRunning();
        if (designLayout) designLayout.classList.toggle('is-generating', running);
        grid.setStatic(running);
        if (addButton) addButton.disabled = running;
        if (cancelButton) cancelButton.disabled = !(generation.status === STATUS.FAILED || generation.status === STATUS.IN_PROGRESS);
        if (messageInput) messageInput.disabled = running;
        if (sendButton) sendButton.disabled = running;
        if (statusBar) statusBar.hidden = !running;
        // Save commits manual edits when idle, publishes a READY draft; disabled
        // while running or on a failed draft (nothing to publish).
        if (saveButton) saveButton.disabled = running || generation.status === STATUS.FAILED;
    };



    /**
     * @param {string | null} fromIso
     * @param {number} toMs
     */
    const formatElapsed = (fromIso, toMs) => {
        const start = fromIso ? new Date(fromIso).getTime() : toMs;
        const secs = Math.max(0, Math.round((toMs - start) / 1000));
        return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
    };

    const updateElapsed = () => {
        if (elapsedEl && isRunning()) elapsedEl.textContent = formatElapsed(generation.startedAt, Date.now());
    };

    // Rebuild the conversation feed from the polled transcript (the source of truth
    // while generating): user / assistant bubbles and interleaved status / error
    // lines. #TODO refactor to use chat_dialog component
    /** @param {FeedMessage[]} messages */
    const renderFeed = (messages) => {
        if (!messagesEl) return;
        messagesEl.innerHTML = '';
        for (const message of messages) {
            const el = document.createElement('div');
            el.className = `chat-message chat-message-${message.type}`;
            el.textContent = message.content;
            messagesEl.appendChild(el);
        }
        scrollConversation();
    };

    // Reconcile the grid to the version's widget set: drop widgets no longer
    // present, (re-)render those that are new or changed.
    /** @param {Record<string, any>[]} widgets */
    const reconcileWidgets = async (widgets) => {
        const incoming = new Set(widgets.map((w) => w.id));
        grid.engine.nodes.slice().forEach((/** @type {any} */ node) => {
            if (!incoming.has(node.id)) {
                grid.removeWidget(node.el);
                rendered.delete(node.id);
            }
        });
        for (const widget of widgets) {
            const signature = JSON.stringify(widget);
            if (rendered.get(widget.id) === signature) continue;
            rendered.set(widget.id, signature);
            await renderWidget(widget);
        }
    };

    const stopPolling = () => {
        if (!generation) return;
        clearTimeout(generation.pollTimer);
        clearInterval(generation.elapsedTimer);
    };

    /** @param {VersionStatusBody} body */
    const applyStatus = async (body) => {
        generation.status = body.status;
        generation.startedAt = body.started_at;
        renderFeed(body.messages);
        await reconcileWidgets(body.widgets);
        if (body.status === STATUS.CANCELLED) {
            // The version was deleted out from under us; revert to the published page.
            window.location.reload();
            return;
        }
        if (body.status !== STATUS.IN_PROGRESS) stopPolling();  // READY / FAILED -> review
        refreshUi();
    };

    const poll = async () => {
        try {
            const url = config.buildUrl('pages_version_status', { page_id: pageId, version_id: generation.versionId });
            const response = await fetch(url);
            if (response.status === 404) { window.location.reload(); return; }
            const body = await response.json();
            await applyStatus(body);
            if (body.status === STATUS.IN_PROGRESS) generation.pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
        } catch (error) {
            generation.pollTimer = setTimeout(poll, POLL_RETRY_MS);
        }
    };

    // Enter the running phase for a new or continued generation: lock, restart the
    // elapsed timer, and poll. Reconcile re-renders the grid from the version's poll,
    // so a follow-up's committed manual edits come back from the server.
    /** @param {number} versionId */
    const enterRunning = (versionId) => {
        stopPolling();  // a follow-up re-enters; clear any prior timers first
        generation = { versionId, status: STATUS.IN_PROGRESS, startedAt: null };
        rendered.clear();
        refreshUi();
        updateElapsed();
        generation.elapsedTimer = setInterval(updateElapsed, 1000);
        poll();
    };

    const publishVersion = async () => {
        const response = await fetch(
            config.buildUrl('pages_version_publish', { page_id: pageId, version_id: generation.versionId }),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // Commit manual moves made during review before publishing.
                body: JSON.stringify({ widgets: getWidgets() })
            }
        );
        if (!response.ok) return;
        window.location.href = config.buildUrl('pages_detail', { page_id: pageId });
    };

    const cancelGeneration = async () => {
        const confirmed = await pagesGridWindow.PHOTO_ORGANIZER.confirmDialog({
            title: t('components:chat.cancelGeneration'),
            message: t('pages:chat.cancelMessage'),
            confirmText: t('components:chat.cancelGeneration'),
            confirmClass: 'btn-danger'
        });
        if (!confirmed) return;
        stopPolling();
        await fetch(
            config.buildUrl('pages_version_cancel', { page_id: pageId, version_id: generation.versionId }),
            { method: 'POST' }
        );
        window.location.reload();  // back to the published version
    };

    const onSave = () => {
        // While a finished generation is under review, Save publishes it; otherwise
        // it commits manual edits.
        if (generation.status === STATUS.READY) return publishVersion();
        return savePage();
    };

    /** @param {Event} event */
    const sendMessage = async (event) => {
        event.preventDefault();
        if (generation.status === STATUS.IN_PROGRESS) return;  // a run is active
        if (!messageInput) return;
        const message = messageInput.value.trim();
        if (!message) return;
        messageInput.value = '';
        try {
            const response = await fetch(config.buildUrl('pages_chat', { page_id: pageId }), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // Send the current widget set (drafts + layout) so the server captures
                // manual edits — on the initial fork and on every follow-up — and the
                // model edits with sight of the code.
                body: JSON.stringify({
                    message,
                    widgets: getWidgets(),
                    widget_errors: pagesGridWindow.PHOTO_ORGANIZER.widgetErrors
                })
            });
            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                pagesGridWindow.notification.error(body.error || t('components:chat.startFailed'));
                messageInput.value = message;  // let the user retry
                return;
            }
            const { version_id } = await response.json();
            enterRunning(version_id);
        } catch (error) {
            pagesGridWindow.notification.error(t('components:chat.startFailed'));
            messageInput.value = message;
        }
    };

    const gridEl = /** @type {HTMLElement} */ (document.querySelector('.grid-stack'));
    grid.on('dragstart resizestart', () => gridEl.classList.add('is-interacting'));
    grid.on('dragstop resizestop', () => gridEl.classList.remove('is-interacting'));

    if (addButton) addButton.addEventListener('click', addWidget);
    if (saveButton) saveButton.addEventListener('click', onSave);
    if (cancelButton) cancelButton.addEventListener('click', cancelGeneration);

    const conversationForm = document.getElementById('conversation-form');
    if (conversationForm) conversationForm.addEventListener('submit', sendMessage);

    grid.engine.nodes.forEach((/** @type {any} */ node) => wireWidget(node.el));
    scrollConversation();
    refreshUi();  // initial (idle) button state

    // Resume: the edit version is a working draft (status is not ACCEPTED) — re-enter
    // and poll; the first response settles the phase (running vs. ready/failed review).
    if (startStatus !== STATUS.ACCEPTED) enterRunning(editVersionId);

    // getVersionId lets the widget broker target the version currently being edited
    // (it changes when a draft is forked, so it's read on demand, not captured).
    return { grid, getVersionId: () => generation.versionId };
};
