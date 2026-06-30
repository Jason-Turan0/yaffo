/**
 * Yaffo widget API.
 *
 * The widget frame calls window.PHOTO_ORGANIZER.pages.initWidgetApi(data, state, locale) with
 * the host-injected data and assigns the result to window.yaffo, BEFORE your code
 * runs. In your widget code you just use window.yaffo — never call init yourself.
 *
 * Widgets run sandboxed: no network, no access to other widgets or the host page.
 * Read your data, run live queries, talk to other widgets, and persist state
 * through this helper, which relays to the host app over postMessage.
 */
window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.pages = window.PHOTO_ORGANIZER.pages || {};

window.PHOTO_ORGANIZER.pages.initWidgetApi = function (data, state, locale) {
    var pending = {};       // requestId -> resolve fn, awaiting a yaffo:result
    var subscribers = {};   // topic -> [handler]
    var nextRequestId = 0;

    window.addEventListener('message', function (event) {
        var msg = event.data || {};
        if (msg.type === 'yaffo:result' && pending[msg.requestId]) {
            pending[msg.requestId](msg.data);
            delete pending[msg.requestId];
        } else if (msg.type === 'yaffo:event') {
            (subscribers[msg.topic] || []).forEach(function (fn) { fn(msg.payload); });
        }
    });

    // Send a request to the host and resolve when its yaffo:result comes back
    // (matched by requestId). `fields` are merged into the message alongside the
    // type/requestId. Resolves to null on a host-side error.
    function request(type, fields) {
        return new Promise(function (resolve) {
            var id = ++nextRequestId;
            pending[id] = resolve;
            var msg = { type: type, requestId: id };
            Object.keys(fields || {}).forEach(function (k) { msg[k] = fields[k]; });
            parent.postMessage(msg, '*');
        });
    }

    var api = {
        /** Results of this widget's data_query, keyed by query name (e.g. yaffo.data.maine_photos). */
        data: data || {},

        /** This widget's persisted state from the last saveState() (re-injected each render). */
        state: state || {},

        /** The application locale selected by the host. */
        locale: locale || 'en',

        /** Locale-aware display formatters. Camera-local dates are not timezone-rebased. */
        number: function (value, options) {
            return new Intl.NumberFormat(api.locale, options || {}).format(value);
        },
        percent: function (value, options) {
            return new Intl.NumberFormat(
                api.locale,
                Object.assign({ style: 'percent' }, options || {})
            ).format(value);
        },
        date: function (value, options) {
            return new Intl.DateTimeFormat(api.locale, options || {}).format(new Date(value));
        },
        relativeTime: function (value, unit, options) {
            return new Intl.RelativeTimeFormat(api.locale, options || {}).format(value, unit);
        },
        list: function (values, options) {
            return new Intl.ListFormat(api.locale, options || {}).format(values);
        },

        /**
         * Run one server query on demand; resolves to its rows (or null on error).
         * `query` is a single { source, ...filters, limit } — the same shape as a
         * value in data_query. Use it to filter or drill down after first render.
         */
        query: function (query) {
            return request('yaffo:query', { query: query });
        },

        /** Broadcast `payload` on `topic` to every widget on the page (pub/sub bus). */
        publish: function (topic, payload) {
            parent.postMessage({ type: 'yaffo:publish', topic: topic, payload: payload }, '*');
        },

        /** Run handler(payload) whenever any widget publishes on `topic`. */
        subscribe: function (topic, handler) {
            (subscribers[topic] = subscribers[topic] || []).push(handler);
        },

        /** Persist this widget's state; it returns as yaffo.state on the next render. */
        saveState: function (newState) {
            api.state = newState;
            parent.postMessage({ type: 'yaffo:state', state: newState }, '*');
        },

        /** The image URL for a photo id (photos carry an id, not a URL). */
        mediaUrl: function (id) { return '/media/' + id; }
    };

    return api;
};
