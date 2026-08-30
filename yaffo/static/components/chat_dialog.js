// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};

// Generic controller for the chat dialog component (templates/components/chat_dialog.html
// + static/components/chat_dialog.css). It owns the parts every consumer shares: the
// transcript feed, the elapsed/status bar, locking the input while a run is active,
// the send/cancel flow, and the poll -> render -> reschedule loop that follows an async
// generation to a terminal status. Domain specifics are passed as hooks, so different
// pages drive the same dialog without copying this logic.
//
// Element ids derive from `id` exactly as the macro emits them: {id}-messages,
// {id}-message, {id}-form, {id}-cancel, {id}-status, {id}-elapsed. A disabled
// (read-only) dialog has no form, so init returns null and wires nothing.
//
// The status payload the poll returns is { status, started_at, messages[] }; hosts may
// add fields and read them in onStatus. Options:
//   startStatus     initial generation status; runningStatus resumes polling on load.
//   statusUrl()     -> URL to poll. A function (not a string) so a host can re-key it
//                      per poll (e.g. a version id that changes when a draft is forked).
//   onSend(text)    async; perform the start request. Return { ok, error? }. On ok the
//                   controller enters the running phase and begins polling.
//   onCancel()      async; perform the cancel request. The controller reloads after.
//   onStatus(body)  optional; host side effects on every poll (e.g. reconcile a grid).
//   onSettled(body) optional; host policy once a poll returns a terminal status (reload
//                   to apply the result, or stay for review).
//   onPhase(status) optional; called whenever the status changes so the host can toggle
//                   its own controls -- the controller already handles the dialog's
//                   input / Send / Cancel / status bar.
//   runningStatus   the status meaning "a run is active" (default 'IN_PROGRESS').
//   cancelConfirm   { title, message, confirmText } for the cancel confirm dialog.
/**
 * @param {string} id
 * @param {ChatDialogOptions} options
 * @returns {ChatDialogApi | null}
 */
window.PHOTO_ORGANIZER.COMPONENTS.initChatDialog = (id, options) => {
    const i18n = window.PHOTO_ORGANIZER.i18n;
    const {
        startStatus,
        statusUrl,
        onSend,
        onCancel,
        onStatus,
        onSettled,
        onPhase,
        runningStatus = 'IN_PROGRESS',
        cancelConfirm = {
            title: i18n.t('components:chat.cancelGeneration'),
            message: i18n.t('components:chat.discardGeneration'),
            confirmText: i18n.t('components:chat.cancelGeneration'),
        },
        pollIntervalMs = 1500,
        pollRetryMs = 3000,
    } = options;

    const form = document.getElementById(`${id}-form`);
    if (!form) return null;  // a disabled (read-only) dialog renders no input
    if (!(form instanceof HTMLFormElement)) return null;

    const messagesEl = document.getElementById(`${id}-messages`);
    const messageInput = document.getElementById(`${id}-message`);
    const sendButton = form.querySelector('button[type="submit"]');
    const cancelButton = document.getElementById(`${id}-cancel`);
    const statusBar = document.getElementById(`${id}-status`);
    const elapsedEl = document.getElementById(`${id}-elapsed`);
    if (
        !(messageInput instanceof HTMLTextAreaElement || messageInput instanceof HTMLInputElement) ||
        !(sendButton instanceof HTMLButtonElement)
    ) return null;

    // status drives the UI phase; startedAt seeds the elapsed counter (the server
    // returns the prompt timestamp so a resumed run keeps ticking from the right time).
    let status = startStatus;
    /** @type {string | null | undefined} */
    let startedAt = null;
    /** @type {ReturnType<typeof setTimeout> | null} */
    let pollTimer = null;
    /** @type {ReturnType<typeof setInterval> | null} */
    let elapsedTimer = null;

    const isRunning = () => status === runningStatus;

    // running -- locked input, only Cancel available, status bar ticking; otherwise
    // the input is open to send (or send a follow-up after a terminal status). Hosts
    // layer their own controls on top via onPhase.
    const refreshUi = () => {
        const running = isRunning();
        messageInput.disabled = running;
        sendButton.disabled = running;
        if (cancelButton instanceof HTMLButtonElement) cancelButton.disabled = !running;
        if (statusBar instanceof HTMLElement) statusBar.hidden = !running;
        if (onPhase) onPhase(status);
    };

    /**
     * @param {string | null | undefined} fromIso
     * @param {number} toMs
     */
    const formatElapsed = (fromIso, toMs) => {
        const start = fromIso ? new Date(fromIso).getTime() : toMs;
        const secs = Math.max(0, Math.round((toMs - start) / 1000));
        return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
    };

    const updateElapsed = () => {
        if (elapsedEl && isRunning()) elapsedEl.textContent = formatElapsed(startedAt, Date.now());
    };

    const scrollFeed = () => {
        if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
    };

    // Rebuild the conversation feed from the polled transcript (the source of truth
    // while generating): user / assistant bubbles and interleaved status / error lines.
    /**
     * @param {ChatStatusBody['messages']} messages
     */
    const renderFeed = (messages) => {
        if (!messagesEl) return;
        messagesEl.innerHTML = '';
        for (const message of messages || []) {
            const el = document.createElement('div');
            el.className = `chat-message chat-message-${message.type}`;
            el.textContent = message.content;
            messagesEl.appendChild(el);
        }
        scrollFeed();
    };

    const stopPolling = () => {
        if (pollTimer !== null) clearTimeout(pollTimer);
        if (elapsedTimer !== null) clearInterval(elapsedTimer);
        pollTimer = null;
        elapsedTimer = null;
    };

    /**
     * @param {ChatStatusBody} body
     */
    const applyStatus = (body) => {
        status = body.status;
        startedAt = body.started_at;
        if (onStatus) onStatus(body);
        renderFeed(body.messages);
        if (!isRunning()) {  // terminal status: stop and hand off to host policy
            stopPolling();
            refreshUi();
            if (onSettled) onSettled(body);
            return;
        }
        refreshUi();
    };

    const poll = async () => {
        try {
            const response = await fetch(statusUrl());
            if (response.status === 404) { window.location.reload(); return; }
            const body = /** @type {ChatStatusBody} */ (await response.json());
            applyStatus(body);
            if (isRunning()) pollTimer = setTimeout(poll, pollIntervalMs);
        } catch {
            pollTimer = setTimeout(poll, pollRetryMs);
        }
    };

    // Enter the running phase for a new or resumed generation: lock, restart the
    // elapsed timer, and poll until a terminal status settles the phase.
    const enterRunning = () => {
        stopPolling();  // a follow-up re-enters; clear any prior timers first
        status = runningStatus;
        refreshUi();
        updateElapsed();
        elapsedTimer = setInterval(updateElapsed, 1000);
        poll();
    };

    /**
     * @param {SubmitEvent} event
     */
    const sendMessage = async (event) => {
        event.preventDefault();
        if (isRunning()) return;  // a run is active
        const message = messageInput.value.trim();
        if (!message) return;
        messageInput.value = '';
        try {
            const result = await onSend(message);
            if (!result || !result.ok) {
                window.notification.error(
                    (result && result.error) || i18n.t('components:chat.startFailed')
                );
                messageInput.value = message;  // let the user retry
                return;
            }
            enterRunning();
        } catch {
            window.notification.error(i18n.t('components:chat.startFailed'));
            messageInput.value = message;
        }
    };

    const cancelGeneration = async () => {
        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            ...cancelConfirm,
            confirmClass: 'btn-danger',
        });
        if (!confirmed) return;
        stopPolling();
        if (onCancel) await onCancel();
        window.location.reload();
    };

    form.addEventListener('submit', sendMessage);
    if (cancelButton) cancelButton.addEventListener('click', cancelGeneration);

    scrollFeed();
    refreshUi();
    if (isRunning()) enterRunning();  // resume an in-flight run on load

    return { enterRunning, isRunning };
};
