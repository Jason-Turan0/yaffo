// @ts-check

(function initDemoMode() {
    if (!window.APP_CONFIG?.demoMode) return;

    const originalFetch = window.fetch.bind(window);
    const handledResponses = new WeakSet();
    const HANDLED_STATUSES = new Set([403, 429]);
    const HANDLED_CODES = new Set(['demo_feature_disabled', 'csrf_failed', 'demo_rate_limit_exceeded']);

    function dispatchDisabled(message, code) {
        document.dispatchEvent(new CustomEvent('yaffo:demo-feature-disabled', {
            detail: { message, code },
        }));
    }

    async function inspectFetchResponse(response) {
        if (!HANDLED_STATUSES.has(response.status) || handledResponses.has(response)) return;
        const contentType = response.headers.get('Content-Type') || '';
        if (!contentType.includes('application/json')) return;
        handledResponses.add(response);
        try {
            const payload = await response.clone().json();
            if (HANDLED_CODES.has(payload.code)) {
                dispatchDisabled(String(payload.error), payload.code);
            }
        } catch {
            // The caller still receives the untouched response.
        }
    }

    window.fetch = async function demoFetch(input, init = {}) {
        const headers = new Headers(input instanceof Request ? input.headers : undefined);
        new Headers(init.headers || {}).forEach((value, key) => headers.set(key, value));
        headers.set('X-Yaffo-Response', 'json');
        if (window.APP_CONFIG.csrfToken) {
            headers.set('X-CSRF-Token', window.APP_CONFIG.csrfToken);
        }
        const response = await originalFetch(input, { ...init, headers });
        await inspectFetchResponse(response);
        return response;
    };

    document.addEventListener('htmx:configRequest', (event) => {
        if (window.APP_CONFIG.csrfToken) {
            event.detail.headers['X-CSRF-Token'] = window.APP_CONFIG.csrfToken;
        }
        event.detail.headers['X-Yaffo-Response'] = 'json';
    });

    document.addEventListener('htmx:beforeSwap', (event) => {
        const xhr = event.detail.xhr;
        if (!HANDLED_STATUSES.has(xhr.status) || xhr.yaffoDemoHandled) return;
        const contentType = xhr.getResponseHeader('Content-Type') || '';
        if (!contentType.includes('application/json')) return;
        try {
            const payload = JSON.parse(xhr.responseText);
            if (HANDLED_CODES.has(payload.code)) {
                xhr.yaffoDemoHandled = true;
                event.detail.shouldSwap = false;
                dispatchDisabled(String(payload.error), payload.code);
            }
        } catch {
            // Leave unrelated error responses to HTMX's normal handling.
        }
    });

    document.addEventListener('yaffo:demo-feature-disabled', (event) => {
        const message = String(event.detail.message);
        if (event.detail.code === 'demo_rate_limit_exceeded') {
            window.notification?.warning(message, 5000);
        } else {
            window.notification?.info(message, 5000);
        }
    });

    const resetTime = document.querySelector('[data-demo-reset-at]');
    if (resetTime instanceof HTMLTimeElement) {
        const date = new Date(resetTime.dateTime);
        if (!Number.isNaN(date.getTime())) {
            resetTime.textContent = new Intl.DateTimeFormat(window.APP_CONFIG.i18n.locale, {
                dateStyle: 'medium',
                timeStyle: 'short',
                timeZone: window.APP_CONFIG.demoTimezone,
                timeZoneName: 'short',
            }).format(date);
        }
    }
})();
