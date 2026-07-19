// @ts-check

(function initRequestSecurity() {
    const originalFetch = window.fetch.bind(window);

    window.fetch = function secureFetch(input, init = {}) {
        const headers = new Headers(input instanceof Request ? input.headers : undefined);
        new Headers(init.headers || {}).forEach((value, key) => headers.set(key, value));
        headers.set('X-Yaffo-Response', 'json');
        if (window.APP_CONFIG.csrfToken) {
            headers.set('X-CSRF-Token', window.APP_CONFIG.csrfToken);
        }
        return originalFetch(input, { ...init, headers });
    };

    document.addEventListener('htmx:configRequest', (event) => {
        if (window.APP_CONFIG.csrfToken) {
            event.detail.headers['X-CSRF-Token'] = window.APP_CONFIG.csrfToken;
        }
        event.detail.headers['X-Yaffo-Response'] = 'json';
    });
})();
