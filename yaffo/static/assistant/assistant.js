// @ts-check

/**
 * Ask Yaffo: the in-app assistant panel (templates/assistant/_panel.html).
 *
 * Floating mode: a side panel on every page, opened from the navbar button, with a
 * conversation switcher. Page mode (/assistant): always open, with the conversation
 * list in the page's sidebar. Both drive the shared chat dialog controller
 * (components/chat_dialog.js), which polls the conversation while a run is active.
 *
 * The transcript is rendered here rather than as plain bubbles: tool calls collapse
 * into one "looked something up" line (each check expands to exactly what was sent
 * to the model, and a script to its source), the doc sections the run used are
 * linked under its answer, and errors are shown in the user's language from their
 * code.
 *
 * The floating panel's width is adjustable from its inner edge (drag, or the arrow
 * keys on the focused handle; double-click resets) and remembered per browser.
 *
 * The floating panel stays open across page loads in the same tab (sessionStorage),
 * except on phone-width screens, where it would cover the page just navigated to.
 *
 * An empty conversation starts with a one-line notice (from the panel template):
 * which model answers, whether it may check this computer, and a link to change
 * that in Settings. Also here: context attached by "Help me with this" buttons, a
 * chip in the composer that goes out with the next message.
 */

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
const assistant = window.PHOTO_ORGANIZER.assistant =
    /** @type {AssistantNamespace} */ (window.PHOTO_ORGANIZER.assistant || {});

const STORAGE_KEY = 'yaffo.assistant.conversation';
// Per tab, so a new tab or window doesn't open with the panel covering it.
const OPEN_KEY = 'yaffo.assistant.open';
// The shared phone breakpoint, where the panel takes the whole width.
const FULL_WIDTH_QUERY = '(max-width: 640px)';
// A viewer preference, so localStorage: every tab uses the width last chosen.
const WIDTH_KEY = 'yaffo.assistant.width';
const DEFAULT_WIDTH = 440;
const MIN_WIDTH = 320;
const MAX_WIDTH = 960;
// Always leave this much of the page visible beside the panel.
const MIN_PAGE_VISIBLE = 160;
const KEYBOARD_STEP = 32;
const RUNNING = 'RUNNING';
const MAX_SOURCES = 5;
const DOC_TOOLS = new Set(['search_docs', 'read_doc']);

/**
 * The chip text for attached context: where the user asked from, and what failed.
 * @param {AssistantContext | null | undefined} context
 * @returns {string}
 */
const contextLabel = (context) => {
    if (!context) return '';
    return [context.page, context.error_code || context.job_id].filter(Boolean).join(' · ');
};

/** @returns {number | null} */
const readStoredConversation = () => {
    try {
        const value = Number(window.localStorage.getItem(STORAGE_KEY));
        return Number.isInteger(value) && value > 0 ? value : null;
    } catch {
        return null;
    }
};

/** @param {number | null} conversationId */
const storeConversation = (conversationId) => {
    try {
        if (conversationId === null) window.localStorage.removeItem(STORAGE_KEY);
        else window.localStorage.setItem(STORAGE_KEY, String(conversationId));
    } catch {
        // Storage can be unavailable (private windows); the panel just starts fresh.
    }
};

/** @returns {number | null} */
const readStoredWidth = () => {
    try {
        const value = Number(window.localStorage.getItem(WIDTH_KEY));
        return Number.isFinite(value) && value > 0 ? value : null;
    } catch {
        return null;
    }
};

/** @param {number | null} width */
const storeWidth = (width) => {
    try {
        if (width === null) window.localStorage.removeItem(WIDTH_KEY);
        else window.localStorage.setItem(WIDTH_KEY, String(Math.round(width)));
    } catch {
        // Storage unavailable: the width just resets on the next page.
    }
};

/** @returns {boolean} */
const readStoredOpen = () => {
    try {
        return window.sessionStorage.getItem(OPEN_KEY) === 'true';
    } catch {
        return false;
    }
};

/** @param {boolean} isOpenNow */
const storeOpen = (isOpenNow) => {
    try {
        if (isOpenNow) window.sessionStorage.setItem(OPEN_KEY, 'true');
        else window.sessionStorage.removeItem(OPEN_KEY);
    } catch {
        // Storage unavailable: the panel just starts closed on the next page.
    }
};

/**
 * @param {string} tag
 * @param {string} [className]
 * @param {string} [text]
 * @returns {HTMLElement}
 */
const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
};

/**
 * @param {Response} response
 * @param {string} fallback
 * @returns {Promise<string>}
 */
const errorMessage = async (response, fallback) => {
    try {
        const body = await response.json();
        return (body && typeof body.error === 'string' && body.error) || fallback;
    } catch {
        return fallback;
    }
};

/**
 * @param {I18nService} i18n
 * @param {AppConfig} config
 * @returns {AssistantApi | null}
 */
assistant.init = (i18n, config) => {
    const panel = document.getElementById('assistant-panel');
    if (!panel) return null;
    const pageMode = panel.dataset.assistantMode === 'page';

    const openButton = document.getElementById('assistant-open');
    const fab = document.getElementById('assistant-fab');
    const closeButton = document.getElementById('assistant-close');
    const newButton = document.getElementById('assistant-new');
    const deleteButton = document.getElementById('assistant-delete');
    const switcher = document.getElementById('assistant-conversation');
    const list = document.getElementById('assistant-conversation-list');
    const listEmpty = document.getElementById('assistant-conversation-empty');
    const messageInput = document.getElementById('assistant-chat-message');
    const form = document.getElementById('assistant-chat-form');
    const noticeTemplate = document.getElementById('assistant-notice-template');
    const contextEl = document.getElementById('assistant-context');
    const contextLabelEl = document.getElementById('assistant-context-label');
    const contextRemove = document.getElementById('assistant-context-remove');
    // The chip sits in the composer, above the message box.
    if (contextEl && form) form.prepend(contextEl);

    /** @type {number | null} */
    let currentId = null;
    /** @type {AssistantConversationSummary[]} */
    let conversations = [];
    /** @type {AssistantContext | null} */
    let pendingContext = null;

    // ---- transcript rendering -------------------------------------------------

    /**
     * @param {ChatMessage} message
     * @returns {string}
     */
    const activityText = (message) => {
        const payload = /** @type {Record<string, any>} */ (message.payload || {});
        if (payload.tool === 'search_docs') {
            return i18n.t('assistant:activity.searched', {
                query: payload.query || '',
                count: Number(payload.count) || 0,
            });
        }
        if (payload.tool === 'read_doc') {
            return payload.error
                ? i18n.t('assistant:activity.readMissing', { title: payload.title || '' })
                : i18n.t('assistant:activity.read', { title: payload.title || '' });
        }
        if (payload.tool === 'run_script') {
            return i18n.t(payload.error ? 'assistant:activity.scriptFailed' : 'assistant:activity.script', {
                purpose: payload.purpose || '',
            });
        }
        if (payload.tool === 'describe_data_source') {
            return i18n.t('assistant:activity.describedSource', { source: payload.title || '' });
        }
        const args = /** @type {Record<string, any>} */ (payload.args || {});
        const line = i18n.t(`assistant:activity.tools.${payload.tool}`, {
            ...args,
            relative_path: args.relative_path || '/',
            defaultValue: i18n.t('assistant:activity.other'),
        });
        return payload.error ? i18n.t('assistant:activity.failed', { line }) : line;
    };

    /**
     * @param {ChatMessage} message
     * @returns {string}
     */
    const errorText = (message) => {
        const payload = /** @type {Record<string, any>} */ (message.payload || {});
        if (!payload.code) return message.content;
        return i18n.t(`assistant:errors.${payload.code}`, {
            provider: payload.provider || '',
            defaultValue: message.content,
        });
    };

    /**
     * @param {ChatMessage[]} tools
     * @returns {HTMLElement}
     */
    const renderActivity = (tools) => {
        const details = el('details', 'assistant-activity');
        const docsOnly = tools.every((tool) => DOC_TOOLS.has(String(tool.payload?.tool)));
        details.appendChild(el('summary', undefined, i18n.t(
            docsOnly ? 'assistant:activity.summary' : 'assistant:activity.steps', { count: tools.length })));
        const lines = el('ul');
        for (const tool of tools) lines.appendChild(renderToolLine(tool));
        details.appendChild(lines);
        return details;
    };

    /**
     * One tool call. A diagnostic or script expands to exactly the text the model
     * received; a script also offers its source.
     * @param {ChatMessage} tool
     * @returns {HTMLElement}
     */
    const renderToolLine = (tool) => {
        const payload = /** @type {Record<string, any>} */ (tool.payload || {});
        const item = el('li');
        if (payload.error) item.classList.add('assistant-tool-error');
        if (!payload.detail && !payload.script) {
            item.textContent = activityText(tool);
            return item;
        }
        const details = el('details', 'assistant-tool');
        details.appendChild(el('summary', undefined, activityText(tool)));
        if (payload.detail) {
            details.appendChild(el('pre', 'assistant-tool-detail', payload.detail));
            details.appendChild(el('p', 'assistant-tool-note', i18n.t('assistant:activity.sentNote', {
                provider: panel.dataset.provider || '',
            })));
        }
        if (payload.script) {
            const script = el('details', 'assistant-tool-script');
            script.appendChild(el('summary', undefined, i18n.t('assistant:activity.showScript')));
            script.appendChild(el('pre', 'assistant-tool-detail', payload.script));
            details.appendChild(script);
        }
        item.appendChild(details);
        return item;
    };

    /**
     * @param {ChatMessage[]} tools
     * @returns {HTMLElement | null}
     */
    const renderLinks = (tools) => {
        /** @type {Map<string, AssistantAppLink>} */
        const byUrl = new Map();
        for (const tool of tools) {
            const payload = /** @type {Record<string, any>} */ (tool.payload || {});
            const links = /** @type {AssistantAppLink[]} */ (Array.isArray(payload.links) ? payload.links : []);
            for (const link of links) byUrl.set(link.url, link);
        }
        if (!byUrl.size) return null;
        const wrapper = el('div', 'assistant-links');
        wrapper.appendChild(el('span', 'assistant-sources-label', i18n.t('assistant:openLinks')));
        const items = el('ul');
        for (const link of byUrl.values()) {
            const item = el('li');
            const anchor = /** @type {HTMLAnchorElement} */ (el('a', undefined, link.title));
            // App-relative, opened in place; the panel stays open across the navigation.
            anchor.href = link.url;
            item.appendChild(anchor);
            items.appendChild(item);
        }
        wrapper.appendChild(items);
        return wrapper;
    };

    /**
     * @param {ChatMessage[]} tools
     * @returns {HTMLElement | null}
     */
    const renderSources = (tools) => {
        /** @type {Map<string, AssistantDocSource>} */
        const byUrl = new Map();
        for (const tool of tools) {
            const payload = /** @type {Record<string, any>} */ (tool.payload || {});
            const sources = /** @type {AssistantDocSource[]} */ (Array.isArray(payload.sources) ? payload.sources : []);
            // A read is a stronger signal than a search hit, so reads go first.
            if (payload.tool === 'read_doc') {
                for (const source of sources) byUrl.set(source.url, source);
            }
        }
        for (const tool of tools) {
            const payload = /** @type {Record<string, any>} */ (tool.payload || {});
            if (payload.tool !== 'search_docs') continue;
            for (const source of /** @type {AssistantDocSource[]} */ (payload.sources || []).slice(0, 2)) {
                if (!byUrl.has(source.url)) byUrl.set(source.url, source);
            }
        }
        if (!byUrl.size) return null;
        const wrapper = el('div', 'assistant-sources');
        wrapper.appendChild(el('span', 'assistant-sources-label', i18n.t('assistant:sources')));
        const items = el('ul');
        for (const source of [...byUrl.values()].slice(0, MAX_SOURCES)) {
            const item = el('li');
            const link = /** @type {HTMLAnchorElement} */ (el('a'));
            link.href = source.url;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = source.heading && source.heading !== source.title
                ? `${source.title} › ${source.heading}`
                : source.title;
            item.appendChild(link);
            items.appendChild(item);
        }
        wrapper.appendChild(items);
        return wrapper;
    };

    /**
     * One run = a user message and everything after it until the next one.
     * @param {ChatMessage[]} messages
     * @returns {Node[]}
     */
    const renderMessages = (messages) => {
        /** @type {Node[]} */
        const nodes = [];
        /** @type {ChatMessage[]} */
        let runTools = [];
        /** @type {ChatMessage[]} */
        let pendingTools = [];
        /** @type {HTMLElement | null} */
        let lastAnswer = null;

        const flushTools = () => {
            if (pendingTools.length) nodes.push(renderActivity(pendingTools));
            pendingTools = [];
        };
        const closeRun = () => {
            flushTools();
            if (lastAnswer) {
                // Links the answer offers first, then the docs it drew on.
                const extras = [renderLinks(runTools), renderSources(runTools)].filter((node) => node !== null);
                nodes.splice(nodes.indexOf(lastAnswer) + 1, 0, ...extras);
            }
            runTools = [];
            lastAnswer = null;
        };

        for (const message of messages) {
            if (message.type === 'user') {
                closeRun();
                const context = /** @type {AssistantContext | undefined} */ (message.payload?.context);
                if (context) nodes.push(el('div', 'assistant-context-sent', contextLabel(context)));
                nodes.push(el('div', 'chat-message chat-message-user', message.content));
            } else if (message.type === 'tool') {
                pendingTools.push(message);
                runTools.push(message);
            } else if (message.type === 'assistant') {
                flushTools();
                lastAnswer = el('div', 'chat-message chat-message-assistant', message.content);
                nodes.push(lastAnswer);
            } else if (message.type === 'error') {
                flushTools();
                nodes.push(el('div', 'chat-message chat-message-error', errorText(message)));
            }
        }
        closeRun();
        return nodes;
    };

    /** @returns {Node[]} */
    const emptyState = () => {
        const intro = el('div', 'assistant-empty');
        if (noticeTemplate instanceof HTMLTemplateElement) {
            intro.appendChild(noticeTemplate.content.cloneNode(true));
        }
        intro.appendChild(el('p', undefined, i18n.t('assistant:empty.intro')));
        const suggestions = el('div', 'assistant-suggestions');
        for (const key of ['addFolders', 'assignFaces', 'automations']) {
            const question = i18n.t(`assistant:empty.suggestions.${key}`);
            const chip = /** @type {HTMLButtonElement} */ (el('button', 'chip chip-action', question));
            chip.type = 'button';
            chip.addEventListener('click', () => {
                if (!(messageInput instanceof HTMLTextAreaElement) || !(form instanceof HTMLFormElement)) return;
                messageInput.value = question;
                form.requestSubmit();
            });
            suggestions.appendChild(chip);
        }
        intro.appendChild(suggestions);
        return [intro];
    };

    // ---- conversations --------------------------------------------------------

    const renderSwitcher = () => {
        if (!(switcher instanceof HTMLSelectElement)) return;
        // The `selected` attribute (not .value) marks the current conversation, so
        // the searchable-select widget's observer picks up the change.
        const options = [new Option(i18n.t('assistant:newConversation'), '', currentId === null, currentId === null)];
        for (const conversation of conversations) {
            const label = conversation.status === RUNNING
                ? i18n.t('assistant:runningTitle', { title: conversation.title })
                : conversation.title;
            const isCurrent = conversation.id === currentId;
            options.push(new Option(label, String(conversation.id), isCurrent, isCurrent));
        }
        switcher.replaceChildren(...options);
    };

    const renderList = () => {
        if (!list) return;
        list.replaceChildren(...conversations.map((conversation) => {
            const item = el('li');
            const button = /** @type {HTMLButtonElement} */ (el('button', 'assistant-conversation-item', conversation.title));
            button.type = 'button';
            if (conversation.id === currentId) button.setAttribute('aria-current', 'true');
            button.addEventListener('click', () => switchTo(conversation.id));
            item.appendChild(button);
            return item;
        }));
        if (listEmpty) listEmpty.hidden = conversations.length > 0;
    };

    const renderConversations = () => {
        renderSwitcher();
        renderList();
        if (deleteButton instanceof HTMLButtonElement) deleteButton.disabled = currentId === null;
    };

    const refreshList = async () => {
        try {
            const response = await fetch(config.urls.assistant_conversations);
            if (!response.ok) return;
            const body = await response.json();
            conversations = Array.isArray(body.conversations) ? body.conversations : [];
        } catch {
            return;
        }
        if (currentId !== null && !conversations.some((c) => c.id === currentId)) {
            currentId = null;
            storeConversation(null);
            chat?.clear(emptyState());
        }
        renderConversations();
    };

    const chat = window.PHOTO_ORGANIZER.COMPONENTS.initChatDialog?.('assistant-chat', {
        startStatus: 'IDLE',
        runningStatus: RUNNING,
        statusUrl: () => config.buildUrl('assistant_conversation', { conversation_id: currentId ?? 0 }),
        renderMessages,
        onSend: async (message) => {
            const url = currentId === null
                ? config.urls.assistant_conversation_create
                : config.buildUrl('assistant_message', { conversation_id: currentId });
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(pendingContext ? { message, context: pendingContext } : { message }),
            });
            if (!response.ok) {
                return { ok: false, error: await errorMessage(response, i18n.t('assistant:sendFailed')) };
            }
            const body = await response.json();
            setContext(null);
            currentId = body.conversation.id;
            storeConversation(currentId);
            refreshList();
            return { ok: true };
        },
        onCancel: () => fetch(config.buildUrl('assistant_cancel', { conversation_id: currentId ?? 0 }), { method: 'POST' }),
        afterCancel: () => chat?.load(),
        onSettled: () => { refreshList(); },
        cancelConfirm: {
            title: i18n.t('assistant:cancel.title'),
            message: i18n.t('assistant:cancel.message'),
            confirmText: i18n.t('assistant:cancel.confirm'),
        },
    }) ?? null;

    /**
     * @param {number | null} conversationId
     * @param {{ focus?: boolean }} [options] focus the message box (default true)
     */
    const switchTo = (conversationId, { focus = true } = {}) => {
        currentId = conversationId;
        storeConversation(conversationId);
        renderConversations();
        if (conversationId === null) chat?.clear(emptyState());
        else chat?.load();
        if (focus && messageInput instanceof HTMLTextAreaElement && isOpen()) messageInput.focus();
    };

    const deleteCurrent = async () => {
        if (currentId === null) return;
        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: i18n.t('assistant:delete.title'),
            message: i18n.t('assistant:delete.message'),
            confirmText: i18n.t('assistant:delete.confirm'),
            confirmClass: 'btn-danger',
        });
        if (!confirmed) return;
        const response = await fetch(config.buildUrl('assistant_delete', { conversation_id: currentId }), {
            method: 'DELETE',
        });
        if (!response.ok && response.status !== 404) {
            window.notification.error(i18n.t('assistant:delete.failed'));
            return;
        }
        switchTo(null);
        await refreshList();
    };

    // ---- attached context ("Help me with this") -----------------------------------

    /** @param {AssistantContext | null} context */
    const setContext = (context) => {
        pendingContext = context;
        if (contextLabelEl) contextLabelEl.textContent = contextLabel(context);
        if (contextEl) contextEl.hidden = !context;
    };

    contextRemove?.addEventListener('click', () => {
        setContext(null);
        if (messageInput instanceof HTMLTextAreaElement) messageInput.focus();
    });

    // ---- width (floating mode) -----------------------------------------------------

    const resizeHandle = document.getElementById('assistant-resize');
    let width = readStoredWidth() ?? DEFAULT_WIDTH;

    const maxWidth = () => Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, window.innerWidth - MIN_PAGE_VISIBLE));

    /** @param {number} requested */
    const applyWidth = (requested) => {
        width = Math.round(Math.min(Math.max(requested, MIN_WIDTH), maxWidth()));
        panel.style.setProperty('--assistant-panel-width', `${width}px`);
        resizeHandle?.setAttribute('aria-valuenow', String(width));
        resizeHandle?.setAttribute('aria-valuemin', String(MIN_WIDTH));
        resizeHandle?.setAttribute('aria-valuemax', String(maxWidth()));
    };

    // The handle is on the panel's inner edge: moving it toward the page (left in
    // LTR, right in RTL) makes the panel wider.
    const isRtl = () => getComputedStyle(panel).direction === 'rtl';

    if (!pageMode && resizeHandle) {
        applyWidth(width);

        resizeHandle.addEventListener('pointerdown', (event) => {
            if (event.button !== 0) return;
            event.preventDefault();
            const startX = event.clientX;
            const startWidth = width;
            const rtl = isRtl();
            resizeHandle.setPointerCapture?.(event.pointerId);
            document.body.classList.add('assistant-resizing');

            /** @param {PointerEvent} move */
            const onMove = (move) => {
                const delta = move.clientX - startX;
                applyWidth(startWidth + (rtl ? delta : -delta));
            };
            const onUp = () => {
                resizeHandle.removeEventListener('pointermove', onMove);
                resizeHandle.removeEventListener('pointerup', onUp);
                resizeHandle.removeEventListener('pointercancel', onUp);
                document.body.classList.remove('assistant-resizing');
                storeWidth(width);
            };
            resizeHandle.addEventListener('pointermove', onMove);
            resizeHandle.addEventListener('pointerup', onUp);
            resizeHandle.addEventListener('pointercancel', onUp);
        });

        resizeHandle.addEventListener('keydown', (event) => {
            const wider = isRtl() ? 'ArrowRight' : 'ArrowLeft';
            const narrower = isRtl() ? 'ArrowLeft' : 'ArrowRight';
            if (event.key === wider) applyWidth(width + KEYBOARD_STEP);
            else if (event.key === narrower) applyWidth(width - KEYBOARD_STEP);
            else if (event.key === 'Home') applyWidth(MIN_WIDTH);
            else if (event.key === 'End') applyWidth(maxWidth());
            else return;
            event.preventDefault();
            storeWidth(width);
        });

        resizeHandle.addEventListener('dblclick', () => {
            applyWidth(DEFAULT_WIDTH);
            storeWidth(null);
        });

        // A narrower window shrinks the panel; the chosen width comes back when it grows.
        window.addEventListener('resize', () => applyWidth(readStoredWidth() ?? DEFAULT_WIDTH));
    }

    // ---- open / close (floating mode) ------------------------------------------

    const isOpen = () => pageMode || !panel.hidden;

    /** @type {HTMLElement | null} */
    let opener = null;

    /**
     * @param {{ focus?: boolean }} [options] move focus into the panel (default
     *   true; false when restoring it on a new page, so the page keeps focus)
     */
    const open = ({ focus = true } = {}) => {
        if (pageMode) return;
        opener = document.activeElement === fab ? fab : openButton;
        // On the narrow shell "Ask Yaffo" is an item in the navbar Menu; close the
        // Menu (and any page panel) so they don't cover the assistant.
        const nav = window.PHOTO_ORGANIZER.COMPONENTS.navPagesBar;
        nav?.applyMenu(false);
        nav?.closeContextPanels();
        panel.hidden = false;
        openButton?.setAttribute('aria-expanded', 'true');
        fab?.setAttribute('aria-expanded', 'true');
        if (fab) fab.hidden = true;  // the panel covers its corner
        storeOpen(true);
        refreshList();
        if (focus && messageInput instanceof HTMLTextAreaElement) messageInput.focus();
    };

    /**
     * Open a new conversation with context attached (a failed job, an error).
     * @param {AssistantContext} context
     * @param {string} [message] a suggested first message, left for the user to send
     */
    const openWithContext = (context, message) => {
        switchTo(null);
        setContext(context);
        if (message && messageInput instanceof HTMLTextAreaElement) messageInput.value = message;
        open();
    };

    // "Help me with this" buttons anywhere on the page (e.g. a failed job card).
    document.addEventListener('click', (event) => {
        const target = event.target instanceof Element ? event.target.closest('[data-assistant-help]') : null;
        if (!(target instanceof HTMLElement)) return;
        event.preventDefault();
        /** @type {AssistantContext} */
        const context = {};
        if (target.dataset.page) context.page = target.dataset.page;
        if (target.dataset.jobId) context.job_id = target.dataset.jobId;
        if (target.dataset.errorCode) context.error_code = target.dataset.errorCode;
        if (target.dataset.error) context.error = target.dataset.error;
        openWithContext(context, i18n.t('assistant:helpPrompt'));
    });

    const close = () => {
        if (pageMode) return;
        storeOpen(false);
        panel.hidden = true;
        openButton?.setAttribute('aria-expanded', 'false');
        fab?.setAttribute('aria-expanded', 'false');
        if (fab) fab.hidden = false;
        // Back to whatever opened it, if still visible; the Menu item isn't once the
        // Menu has closed, so fall back to the corner button or the Menu toggle.
        const returnTo = [opener, fab, document.getElementById('nav-menu-toggle')]
            .find((candidate) => candidate instanceof HTMLElement && candidate.offsetParent !== null);
        if (returnTo instanceof HTMLElement) returnTo.focus();
    };

    openButton?.addEventListener('click', () => {
        if (pageMode) return;
        if (isOpen()) close();
        else open();
    });
    fab?.addEventListener('click', () => open());
    closeButton?.addEventListener('click', close);
    newButton?.addEventListener('click', () => switchTo(null));
    deleteButton?.addEventListener('click', deleteCurrent);
    switcher?.addEventListener('change', () => {
        if (!(switcher instanceof HTMLSelectElement)) return;
        switchTo(switcher.value ? Number(switcher.value) : null);
    });
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || pageMode || panel.hidden) return;
        if (document.querySelector('.modal.active')) return;  // a confirm dialog owns Escape
        close();
    });

    // Error toasts offer "Help me with this" once the assistant can answer.
    if (fab || openButton) {
        window.notification.setErrorAction?.({
            label: i18n.t('assistant:helpWithThis'),
            run: (error) => openWithContext({ page: document.title, error }, i18n.t('assistant:helpPrompt')),
        });
    }

    // Reopen the panel if it was open on the previous page, straight away so it
    // doesn't flicker, but without taking focus from the new page.
    if (!pageMode && readStoredOpen() && !window.matchMedia?.(FULL_WIDTH_QUERY).matches) {
        open({ focus: false });
    }

    // Start on the last conversation if it still exists, else a fresh one.
    const start = async () => {
        await refreshList();
        const stored = readStoredConversation();
        // An attached context (a click that came in first) starts its own conversation.
        if (pendingContext === null) {
            switchTo(stored !== null && conversations.some((c) => c.id === stored) ? stored : null, { focus: false });
        }
    };
    start();

    return { open, close, isOpen, switchTo, refreshList, openWithContext };
};
