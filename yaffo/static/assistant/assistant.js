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
 * into one "looked something up" line, the doc sections the run used are linked
 * under its answer, and errors are shown in the user's language from their code.
 */

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
const assistant = window.PHOTO_ORGANIZER.assistant =
    /** @type {AssistantNamespace} */ (window.PHOTO_ORGANIZER.assistant || {});

const STORAGE_KEY = 'yaffo.assistant.conversation';
const RUNNING = 'RUNNING';
const MAX_SOURCES = 5;

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

    /** @type {number | null} */
    let currentId = null;
    /** @type {AssistantConversationSummary[]} */
    let conversations = [];

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
        return i18n.t('assistant:activity.other');
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
        details.appendChild(el('summary', undefined, i18n.t('assistant:activity.summary', { count: tools.length })));
        const lines = el('ul');
        for (const tool of tools) lines.appendChild(el('li', undefined, activityText(tool)));
        details.appendChild(lines);
        return details;
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
            const sources = lastAnswer ? renderSources(runTools) : null;
            if (sources && lastAnswer) nodes.splice(nodes.indexOf(lastAnswer) + 1, 0, sources);
            runTools = [];
            lastAnswer = null;
        };

        for (const message of messages) {
            if (message.type === 'user') {
                closeRun();
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
                body: JSON.stringify({ message }),
            });
            if (!response.ok) {
                return { ok: false, error: await errorMessage(response, i18n.t('assistant:sendFailed')) };
            }
            const body = await response.json();
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

    /** @param {number | null} conversationId */
    const switchTo = (conversationId) => {
        currentId = conversationId;
        storeConversation(conversationId);
        renderConversations();
        if (conversationId === null) chat?.clear(emptyState());
        else chat?.load();
        if (messageInput instanceof HTMLTextAreaElement && isOpen()) messageInput.focus();
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

    // ---- open / close (floating mode) ------------------------------------------

    const isOpen = () => pageMode || !panel.hidden;

    /** @type {HTMLElement | null} */
    let opener = null;

    const open = () => {
        if (pageMode) return;
        opener = document.activeElement === fab ? fab : openButton;
        panel.hidden = false;
        openButton?.setAttribute('aria-expanded', 'true');
        fab?.setAttribute('aria-expanded', 'true');
        if (fab) fab.hidden = true;  // the panel covers its corner
        refreshList();
        if (messageInput instanceof HTMLTextAreaElement) messageInput.focus();
    };

    const close = () => {
        if (pageMode) return;
        panel.hidden = true;
        openButton?.setAttribute('aria-expanded', 'false');
        fab?.setAttribute('aria-expanded', 'false');
        if (fab) fab.hidden = false;
        const returnTo = opener && opener.offsetParent !== null ? opener : fab || openButton;
        if (returnTo instanceof HTMLElement) returnTo.focus();
    };

    openButton?.addEventListener('click', () => {
        if (pageMode) return;
        if (isOpen()) close();
        else open();
    });
    fab?.addEventListener('click', open);
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

    // Start on the last conversation if it still exists, else a fresh one.
    const start = async () => {
        await refreshList();
        const stored = readStoredConversation();
        switchTo(stored !== null && conversations.some((c) => c.id === stored) ? stored : null);
    };
    start();

    return { open, close, isOpen, switchTo, refreshList };
};
