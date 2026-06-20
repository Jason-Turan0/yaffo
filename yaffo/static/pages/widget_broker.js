window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

// Runtime widget errors, kept locally (in memory, per session) keyed by widget
// id. Not persisted — they're only sent along as context the next time the model
// generates, so it can fix code that threw.
window.PHOTO_ORGANIZER.widgetErrors = window.PHOTO_ORGANIZER.widgetErrors || {};

// Broker for widget iframes — the host-side counterpart of widget_api.js
// (window.yaffo). Sandboxed frames can't fetch (connect-src 'none') or see each
// other, so the parent brokers messages between them and the server. Used in both
// modes. One handler per message type, dispatched by `type`.
//
// `getVersionId()` returns the version currently being displayed — published in
// presentation, the edit version (draft or published) in design, re-read each call
// so it tracks a draft forked mid-session. Widget state/query are scoped to that
// version's widget (widgets are unique per version).
window.PHOTO_ORGANIZER.initWidgetBroker = (pageId, getVersionId, config) => {
    const frames = () => [...document.querySelectorAll('.widget-frame')];
    const senderFrame = (event) => frames().find((f) => f.contentWindow === event.source);

    // yaffo:publish -> fan out as yaffo:event to every widget (pub/sub bus)
    const handlePublish = (event, msg) => {
        frames().forEach((f) =>
            f.contentWindow.postMessage(
                { type: 'yaffo:event', topic: msg.topic, payload: msg.payload }, '*'
            ));
    };

    // yaffo:state -> persist the sending widget's state (fire-and-forget)
    const handleState = (event, msg) => {
        const frame = senderFrame(event);
        if (!frame) return;
        fetch(
            config.buildUrl('pages_version_widget_state', { page_id: pageId, version_id: getVersionId(), widget_id: frame.dataset.widgetId }),
            { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ state: msg.state }) }
        );
    };

    // POST `body` to a version/widget-scoped endpoint for the sending widget and
    // reply yaffo:result with the response's `data` (null on any failure). The
    // request/response round trip every read-style message shares.
    const brokerResult = async (event, msg, endpoint, body) => {
        const frame = senderFrame(event);
        if (!frame) return;
        let data = null;
        try {
            const response = await fetch(
                config.buildUrl(endpoint, { page_id: pageId, version_id: getVersionId(), widget_id: frame.dataset.widgetId }),
                { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
            );
            data = (await response.json()).data;
        } catch (e) {
            data = null;
        }
        frame.contentWindow.postMessage({ type: 'yaffo:result', requestId: msg.requestId, data }, '*');
    };

    // yaffo:query -> resolve a live data_query, reply yaffo:result to the sender.
    // Media dirs + the folder tree are just sources (media_dirs / folders), so they
    // go through this same path — no dedicated message types.
    const handleQuery = (event, msg) =>
        brokerResult(event, msg, 'pages_version_widget_query', { query: msg.query });

    // yaffo:error -> record the sending widget's runtime error locally
    const handleError = (event, msg) => {
        const frame = senderFrame(event);
        if (!frame) return;
        const store = window.PHOTO_ORGANIZER.widgetErrors;
        const id = frame.dataset.widgetId;
        store[id] = [...(store[id] || []), msg.message].slice(-10);
    };

    const handlers = {
        'yaffo:publish': handlePublish,
        'yaffo:state': handleState,
        'yaffo:query': handleQuery,
        'yaffo:error': handleError,
    };

    window.addEventListener('message', (event) => {
        const msg = event.data || {};
        const handler = handlers[msg.type];
        if (handler) handler(event, msg);
    });
};